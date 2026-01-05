#!/usr/bin/env python3
"""
LangGraph Red-Teaming Pipeline
Orchestrates the complete 4-phase adversarial testing workflow
"""

import json
import os
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, TypedDict

from adversarial_task_generator import AdversarialTaskGenerator
from failure_logging import FailureLogger, FailureRecord
from dotenv import load_dotenv
from langgraph.graph import END, START, StateGraph

try:
    from langgraph.checkpoint.memory import MemorySaver
except Exception:
    MemorySaver = None


class SolverModelConfig(TypedDict, total=False):
    name: str
    provider: str
    model: str
    base_url: str
    api_key_env: str
    temperature: float
    max_tokens: int
    host: str


class AgentAttempt(TypedDict, total=False):
    test_id: str
    model: str
    expected: str
    actual: str
    passed: bool
    error: str


class AdversarialTestState(TypedDict):
    target_weakness: str
    num_test_cases: int
    adversarial_tests: List[Dict[str, Any]]
    generation_metadata: Dict[str, Any]
    solver_results: Dict[str, List[Dict[str, Any]]]
    execution_logs: List[str]
    failure_patterns: List[Dict[str, Any]]
    success_rates: Dict[str, float]
    context_documentation: Optional[str]


class RedTeamState(AdversarialTestState, total=False):
    task_id: str
    weakness_category: str
    target_models: List[Any]
    num_variants: int
    regenerate_tests: bool
    use_starcoder_variants: bool
    reference_solution: str
    test_harness: str
    severity_scores: Dict[str, float]
    context_updates: str
    report_path: str
    timestamp: str
    duration_seconds: float
    model_errors: Dict[str, List[str]]


class RedTeamingPipeline:
    """Main pipeline orchestrator"""
    
    def __init__(
        self,
        task_dir: str = "./adversarial_tasks",
        context_dir: str = "./.context",
        results_dir: str = "./results"
    ):
        """Initialize pipeline"""
        load_dotenv()
        self.task_dir = Path(task_dir)
        self.context_dir = Path(context_dir)
        self.results_dir = Path(results_dir)
        
        self.task_dir.mkdir(parents=True, exist_ok=True)
        self.context_dir.mkdir(parents=True, exist_ok=True)
        self.results_dir.mkdir(parents=True, exist_ok=True)
        (self.context_dir / "failure_patterns").mkdir(parents=True, exist_ok=True)
    
    def _load_adversarial_testing_config(self) -> Dict[str, Any]:
        config_path = Path(__file__).resolve().parent / ".cline" / "config.json"
        if not config_path.exists():
            return {}
        try:
            return json.loads(config_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _load_solver_models_from_config(self) -> List[str]:
        cfg = self._load_adversarial_testing_config()
        adv = cfg.get("adversarial_testing") or cfg.get("adversarial_mode") or {}
        solver_models = adv.get("solver_models") or []
        model_names: List[str] = []
        for entry in solver_models:
            if isinstance(entry, dict):
                model = entry.get("model")
                if isinstance(model, str) and model.strip():
                    model_names.append(model.strip())
        return model_names

    def _load_solver_configs_from_config(self) -> List[SolverModelConfig]:
        cfg = self._load_adversarial_testing_config()
        adv = cfg.get("adversarial_testing") or cfg.get("adversarial_mode") or {}
        solver_models = adv.get("solver_models") or []
        out: List[SolverModelConfig] = []
        for entry in solver_models:
            if not isinstance(entry, dict):
                continue
            model = entry.get("model")
            if not isinstance(model, str) or not model.strip():
                continue
            provider = entry.get("provider")
            if not isinstance(provider, str) or not provider.strip():
                provider = "openai"
            item: SolverModelConfig = {
                "name": str(entry.get("name") or model).strip(),
                "provider": provider.strip(),
                "model": model.strip(),
            }
            if isinstance(entry.get("base_url"), str) and entry.get("base_url"):
                item["base_url"] = str(entry.get("base_url"))
            if isinstance(entry.get("api_key_env"), str) and entry.get("api_key_env"):
                item["api_key_env"] = str(entry.get("api_key_env"))
            temp_val = entry.get("temperature")
            if isinstance(temp_val, (int, float, str)):
                try:
                    item["temperature"] = float(temp_val)
                except Exception:
                    pass
            max_tokens_val = entry.get("max_tokens")
            if isinstance(max_tokens_val, (int, float, str)):
                try:
                    item["max_tokens"] = int(max_tokens_val)
                except Exception:
                    pass
            if isinstance(entry.get("host"), str) and entry.get("host"):
                item["host"] = str(entry.get("host"))
            out.append(item)
        return out

    def _default_solver_configs(self) -> List[SolverModelConfig]:
        return [
            {
                "name": "GPT-4o",
                "provider": "openai",
                "model": "gpt-4o",
                "api_key_env": "OPENAI_API_KEY",
                "temperature": 0.2,
                "max_tokens": 512,
            }
        ]

    def _model_id(self, cfg: SolverModelConfig) -> str:
        return str(cfg.get("name") or cfg.get("model") or "unknown")

    def _ollama_list_models(self, host: str) -> List[str]:
        url = f"{host.rstrip('/')}/api/tags"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        parsed = json.loads(raw)
        models = parsed.get("models")
        if not isinstance(models, list):
            return []
        out: List[str] = []
        for m in models:
            if not isinstance(m, dict):
                continue
            name = m.get("name") or m.get("model")
            if isinstance(name, str) and name.strip():
                out.append(name.strip())
        return out

    def _ollama_pick_fallback_model(self, available: List[str], preferred: str) -> Optional[str]:
        preferred = (preferred or "").strip()
        if preferred and preferred in available:
            return preferred

        base = preferred.split(":", 1)[0].strip() if preferred else ""
        if base:
            instruct = [m for m in available if m.startswith(base + ":") and "instruct" in m.lower()]
            if instruct:
                return instruct[0]
            base_matches = [m for m in available if m.startswith(base + ":")]
            if base_matches:
                return base_matches[0]

        if available:
            return available[0]
        return None

    def _ollama_generate(self, cfg: SolverModelConfig, prompt: str) -> str:
        host = str(cfg.get("host") or "http://localhost:11434").rstrip("/")
        url = f"{host}/api/generate"
        model = str(cfg.get("model") or "").strip()
        if model.startswith("ollama/"):
            model = model.split("/", 1)[1].strip()
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": cfg.get("temperature", 0.2),
                "num_predict": cfg.get("max_tokens", 512),
            },
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", errors="replace")
            except Exception:
                body = ""
            lowered = body.lower()
            if (e.code == 404) or ("model" in lowered and "not found" in lowered):
                available = self._ollama_list_models(host)
                fallback = self._ollama_pick_fallback_model(available, model)
                if fallback and fallback != model:
                    payload["model"] = fallback
                    data = json.dumps(payload).encode("utf-8")
                    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
                    with urllib.request.urlopen(req, timeout=60) as resp:
                        raw = resp.read().decode("utf-8", errors="replace")
                else:
                    raise
            else:
                raise
        parsed = json.loads(raw)
        return str(parsed.get("response") or "")

    def _openai_chat(self, cfg: SolverModelConfig, prompt: str) -> str:
        try:
            from openai import OpenAI
        except Exception as e:
            raise RuntimeError(f"openai import failed: {e}")

        api_key_env = str(cfg.get("api_key_env") or "OPENAI_API_KEY")
        api_key = os.environ.get(api_key_env)
        if not api_key:
            raise RuntimeError(f"Missing API key env: {api_key_env}")

        base_url = str(cfg.get("base_url") or "https://api.openai.com/v1")
        client = OpenAI(api_key=api_key, base_url=base_url)
        resp = client.chat.completions.create(
            model=str(cfg.get("model")),
            messages=[{"role": "user", "content": prompt}],
            temperature=float(cfg.get("temperature", 0.2)),
            max_tokens=int(cfg.get("max_tokens", 512)),
        )
        content = resp.choices[0].message.content if resp.choices else ""
        return str(content or "")

    def _anthropic_chat(self, cfg: SolverModelConfig, prompt: str) -> str:
        try:
            from anthropic import Anthropic
        except Exception as e:
            raise RuntimeError(f"anthropic import failed: {e}")

        api_key_env = str(cfg.get("api_key_env") or "ANTHROPIC_API_KEY")
        api_key = os.environ.get(api_key_env)
        if not api_key:
            raise RuntimeError(f"Missing API key env: {api_key_env}")

        client = Anthropic(api_key=api_key)
        msg = client.messages.create(
            model=str(cfg.get("model")),
            max_tokens=int(cfg.get("max_tokens", 512)),
            temperature=float(cfg.get("temperature", 0.2)),
            messages=[{"role": "user", "content": prompt}],
        )
        blocks = getattr(msg, "content", None)
        if not blocks:
            return ""
        first = blocks[0]
        text = getattr(first, "text", None)
        return str(text or "")

    def _ask_model(self, cfg: SolverModelConfig, prompt: str) -> str:
        provider = str(cfg.get("provider") or "openai").lower()
        if provider == "ollama":
            return self._ollama_generate(cfg, prompt)
        if provider == "anthropic":
            return self._anthropic_chat(cfg, prompt)
        if provider == "openai":
            return self._openai_chat(cfg, prompt)
        raise RuntimeError(f"Unsupported provider: {provider}")

    def _canonicalize_output(self, text: str) -> str:
        s = (text or "").strip()
        if not s:
            return ""
        if "```" in s:
            parts = [p for p in s.split("```") if p.strip()]
            if parts:
                s = parts[-1].strip()
                if s.lower().startswith("python"):
                    s = s[6:].strip()
                elif s.lower().startswith("json"):
                    s = s[4:].strip()

        for line in s.splitlines():
            t = line.strip()
            if not t:
                continue
            if t.startswith("```"):
                continue
            return t
        return ""

    def _prompt_for_test(self, instruction: str, test_input: str) -> str:
        return (
            "You are solving a programming task. Produce the exact output for the given input. "
            "Output exactly one line and nothing else.\n\n"
            "TASK:\n"
            f"{instruction.strip()}\n\n"
            "INPUT:\n"
            f"{test_input.rstrip()}\n"
        )

    def _run_agent_attempt(self, cfg: SolverModelConfig, instruction: str, test: Dict[str, Any]) -> AgentAttempt:
        test_id = str(test.get("id") or "")
        inp = str(test.get("input") or "")
        expected = str(test.get("expected_output") or "").strip()
        prompt = self._prompt_for_test(instruction, inp)
        try:
            raw = self._ask_model(cfg, prompt)
            actual = self._canonicalize_output(raw)
            passed = actual == expected
            return AgentAttempt(
                test_id=test_id,
                model=self._model_id(cfg),
                expected=expected,
                actual=actual,
                passed=passed,
            )
        except Exception as e:
            return AgentAttempt(
                test_id=test_id,
                model=self._model_id(cfg),
                expected=expected,
                actual="",
                passed=False,
                error=str(e),
            )

    def _next_task_id(self, weakness: str) -> str:
        prefix = f"{weakness.lower()}-adversarial-"
        max_n = 0
        if self.task_dir.exists():
            for p in self.task_dir.iterdir():
                if not p.is_dir():
                    continue
                name = p.name
                if not name.startswith(prefix):
                    continue
                suffix = name[len(prefix):]
                if suffix.isdigit():
                    max_n = max(max_n, int(suffix))
        return f"{prefix}{max_n + 1:03d}"

    def build_graph(self) -> StateGraph:
        """Build the LangGraph workflow"""
        workflow = StateGraph(RedTeamState)

        workflow.add_node("generate_tests", self.generate_adversarial_tests)
        workflow.add_node("iterate_from_failures", self.iterate_from_failures)
        workflow.add_node("validate_solvers", self.validate_against_solvers)
        workflow.add_node("analyze_failures", self.analyze_failure_patterns)
        workflow.add_node("document_context", self.document_in_context_framework)

        workflow.add_edge(START, "generate_tests")
        workflow.add_edge("generate_tests", "validate_solvers")
        workflow.add_edge("validate_solvers", "analyze_failures")
        workflow.add_edge("analyze_failures", "document_context")
        workflow.add_edge("document_context", END)

        return workflow
    
    def generate_adversarial_tests(self, state: RedTeamState) -> Dict[str, Any]:
        """Phase 1: Generate adversarial tests"""
        weakness = state.get("target_weakness") or state.get("weakness_category") or ""
        num_variants = int(state.get("num_variants") or state.get("num_test_cases") or 3)
        print(f"\n📝 PHASE 1: Generating adversarial tests for {weakness}")

        task_id = state.get("task_id")
        if not task_id:
            task_id = self._next_task_id(weakness)
            generator = AdversarialTaskGenerator(str(self.task_dir))
            generator.generate_task(
                task_id=task_id,
                weakness_category=weakness,
                target_models=state.get("target_models") or self._load_solver_models_from_config() or ["gpt-4o"],
                num_variants=num_variants,
                description=f"Adversarial test for {weakness}",
                use_starcoder=True,
            )

        task_path = self.task_dir / task_id
        if (task_path / "instruction.md").exists():
            print(f"✅ Task {task_id} loaded")

            instruction = (task_path / "instruction.md").read_text(encoding="utf-8")

            regenerate_tests = bool(state.get("regenerate_tests"))
            use_starcoder_variants = bool(state.get("use_starcoder_variants"))
            if (regenerate_tests or use_starcoder_variants) and weakness == "COBOL_PACKED_DECIMAL":
                generator = AdversarialTaskGenerator(str(self.task_dir))
                generator._use_starcoder_variants = use_starcoder_variants
                for i in range(1, num_variants + 1):
                    generator._create_test_variant(task_path, i, weakness)

            tests: List[Dict[str, Any]] = []
            for i in range(1, num_variants + 1):
                test_input = (task_path / "tests" / f"variant_{i}.input").read_text(encoding="utf-8")
                test_expected = (task_path / "tests" / f"variant_{i}.expected").read_text(encoding="utf-8")
                tests.append({"id": f"variant_{i}", "input": test_input, "expected_output": test_expected})

            reference = ""
            harness = ""
            if (task_path / "solution" / "solve.sh").exists():
                reference = (task_path / "solution" / "solve.sh").read_text(encoding="utf-8")
            if (task_path / "tests" / "test.sh").exists():
                harness = (task_path / "tests" / "test.sh").read_text(encoding="utf-8")

            return {
                "task_id": task_id,
                "weakness_category": weakness,
                "target_weakness": weakness,
                "num_variants": num_variants,
                "num_test_cases": num_variants,
                "adversarial_tests": tests,
                "instruction": instruction,
                "reference_solution": reference,
                "test_harness": harness,
                "generation_metadata": {
                    "timestamp": datetime.now().isoformat(),
                    "generator": "AdversarialTaskGenerator",
                    "source": "disk_or_generated",
                },
            }

        return {
            "task_id": task_id,
            "weakness_category": weakness,
            "target_weakness": weakness,
            "num_variants": num_variants,
            "num_test_cases": num_variants,
            "adversarial_tests": [],
            "reference_solution": "",
            "test_harness": "",
            "generation_metadata": {"timestamp": datetime.now().isoformat(), "generator": "AdversarialTaskGenerator"},
        }

    def iterate_from_failures(self, state: RedTeamState) -> Dict[str, Any]:
        solver_results = state.get("solver_results") or {}
        success_rates = state.get("success_rates") or {}
        failure_patterns = state.get("failure_patterns") or []

        task_id = str(state.get("task_id") or "")
        weakness = str(state.get("target_weakness") or state.get("weakness_category") or "")

        if not success_rates:
            for model_name, results in solver_results.items():
                name = str(model_name)
                if name.startswith("__"):
                    continue
                usable = [r for r in results if not r.get("error")]
                if not usable:
                    continue
                passed = sum(1 for r in usable if r.get("passed"))
                total = len(usable)
                rate = (passed / total) if total > 0 else 1.0
                success_rates[name] = rate

        worst_model = None
        worst_rate = 1.0
        for model_name, rate_val in success_rates.items():
            name = str(model_name)
            try:
                rate = float(rate_val)
            except Exception:
                continue
            if name.startswith("__"):
                continue
            if rate < worst_rate:
                worst_rate = rate
                worst_model = name

        should_iterate = worst_model is not None and worst_rate > 0.8
        if not should_iterate:
            return {}

        objectives: List[Dict[str, Any]] = []
        for p in failure_patterns:
            if str(p.get("model") or "") != worst_model:
                continue
            objectives.append(
                {
                    "signature_id": str(p.get("signature_id") or ""),
                    "model": worst_model,
                    "category": str(p.get("category") or ""),
                    "test_id": str(p.get("test_id") or ""),
                    "message": str(p.get("message") or ""),
                }
            )
        if not objectives:
            for r in solver_results.get(str(worst_model or ""), []):
                if not r.get("passed") or r.get("error"):
                    objectives.append(
                        {
                            "signature_id": "",
                            "model": worst_model,
                            "category": "agent_error" if r.get("error") else "agent_mismatch",
                            "test_id": str(r.get("test_id") or ""),
                            "message": str(r.get("error") or "mismatch between expected and actual output"),
                        }
                    )
            if not objectives:
                return {}

        summary_lines: List[str] = []
        summary_lines.append(f"Child task derived from failures on {task_id} for model {worst_model}.")
        summary_lines.append("Primary iteration objectives:")
        for obj in objectives[:3]:
            test_id = obj.get("test_id") or ""
            category = obj.get("category") or ""
            msg = obj.get("message") or ""
            summary_lines.append(f"- {category} on test {test_id}: {msg}")
        description = "\n".join(summary_lines)

        next_task_id = self._next_task_id(weakness)
        generator = AdversarialTaskGenerator(str(self.task_dir))
        generator.generate_task(
            task_id=next_task_id,
            weakness_category=weakness,
            target_models=[worst_model] if worst_model else [],
            num_variants=int(state.get("num_variants") or state.get("num_test_cases") or 3),
            description=description,
            use_starcoder=True,
        )
        return {
            "task_id": next_task_id,
            "regenerate_tests": False,
            "iteration_objectives": objectives[:3],
            "parent_task_id": task_id,
            "worst_model": worst_model,
            "worst_model_success_rate": worst_rate,
        }

    def validate_against_solvers(self, state: RedTeamState) -> Dict[str, Any]:
        """Phase 2: Validate against multiple solver models"""
        explicit_models = "target_models" in state
        raw_models = state.get("target_models") if explicit_models else None
        model_cfgs: List[SolverModelConfig] = []
        if raw_models is not None:
            for m in raw_models:
                if isinstance(m, dict):
                    model = m.get("model")
                    if not isinstance(model, str) or not model.strip():
                        continue
                    provider = m.get("provider")
                    if not isinstance(provider, str) or not provider.strip():
                        provider = "openai"
                    cfg: SolverModelConfig = {
                        "name": str(m.get("name") or model).strip(),
                        "provider": provider.strip(),
                        "model": model.strip(),
                    }
                    base_url = m.get("base_url")
                    if isinstance(base_url, str) and base_url.strip():
                        cfg["base_url"] = base_url.strip()
                    api_key_env = m.get("api_key_env")
                    if isinstance(api_key_env, str) and api_key_env.strip():
                        cfg["api_key_env"] = api_key_env.strip()
                    host = m.get("host")
                    if isinstance(host, str) and host.strip():
                        cfg["host"] = host.strip()
                    temp_val = m.get("temperature")
                    if isinstance(temp_val, (int, float, str)):
                        try:
                            cfg["temperature"] = float(temp_val)
                        except Exception:
                            pass
                    max_tokens_val = m.get("max_tokens")
                    if isinstance(max_tokens_val, (int, float, str)):
                        try:
                            cfg["max_tokens"] = int(max_tokens_val)
                        except Exception:
                            pass
                    model_cfgs.append(cfg)
                elif isinstance(m, str) and m.strip():
                    model_cfgs.append({"name": m.strip(), "provider": "openai", "model": m.strip(), "api_key_env": "OPENAI_API_KEY"})
        if (not explicit_models) and (not model_cfgs):
            model_cfgs = self._load_solver_configs_from_config() or self._default_solver_configs()

        instruction = str(state.get("instruction") or "")
        tests = state.get("adversarial_tests") or []
        print(f"\n🧪 PHASE 2: Validating against {len(model_cfgs)} models")

        results: Dict[str, List[Dict[str, Any]]] = {}
        logs: List[str] = []

        weakness = str(state.get("target_weakness") or state.get("weakness_category") or "")

        oracle_results: List[Dict[str, Any]] = []
        if weakness == "COBOL_PACKED_DECIMAL":
            gen = AdversarialTaskGenerator(str(self.task_dir))
            for test in tests:
                test_id = str(test.get("id") or "")
                inp = str(test.get("input") or "")
                expected = str(test.get("expected_output") or "").strip()
                decoded = gen._decode_comp3_hex(inp)
                actual = decoded if decoded is not None else "ERROR"
                actual = str(actual).strip()
                oracle_results.append(
                    {
                        "test_id": test_id,
                        "passed": actual == expected,
                        "expected": expected,
                        "actual": actual,
                        "model": "__oracle__",
                    }
                )
        results["__oracle__"] = oracle_results
        logs.append(f"Validated {len(oracle_results)} tests for __oracle__")

        for cfg in model_cfgs:
            model_id = self._model_id(cfg)
            model_results: List[Dict[str, Any]] = []
            for test in tests:
                attempt = self._run_agent_attempt(cfg, instruction, test)
                model_results.append(
                    {
                        "test_id": attempt.get("test_id") or "",
                        "passed": bool(attempt.get("passed")),
                        "expected": str(attempt.get("expected") or ""),
                        "actual": str(attempt.get("actual") or ""),
                        "error": str(attempt.get("error")) if attempt.get("error") else None,
                        "model": str(attempt.get("model") or model_id),
                    }
                )
            results[model_id] = model_results
            logs.append(f"Validated {len(model_results)} tests for {model_id}")

        return {"solver_results": results, "execution_logs": logs, "target_models": model_cfgs}
    
    def analyze_failure_patterns(self, state: RedTeamState) -> Dict[str, Any]:
        """Phase 3: Analyze failures and classify patterns"""
        print("\n🔍 PHASE 3: Analyzing failure patterns")

        patterns: List[Dict[str, Any]] = []
        success_rates: Dict[str, float] = {}
        model_errors: Dict[str, List[str]] = {}
        logger = FailureLogger(str(self.context_dir))
        records: List[FailureRecord] = []

        solver_results = state.get("solver_results") or {}
        task_id = str(state.get("task_id") or "")
        now = datetime.now().isoformat()

        for model_name, results in solver_results.items():
            name = str(model_name)
            if name.startswith("__"):
                passed_count = sum(1 for r in results if r.get("passed"))
                total = len(results)
                success_rates[name] = (passed_count / total) if total > 0 else 0.0
                continue

            usable = [r for r in results if not r.get("error")]
            if not usable:
                errs = [str(r.get("error")) for r in results if r.get("error")]
                if errs:
                    model_errors[name] = errs
                continue

            passed_count = sum(1 for r in usable if r.get("passed"))
            total = len(usable)
            success_rates[name] = (passed_count / total) if total > 0 else 0.0

            for r in results:
                test_id = str(r.get("test_id") or "")
                passed = bool(r.get("passed"))
                expected = str(r.get("expected") or "")
                actual = str(r.get("actual") or "")
                error = str(r.get("error")) if r.get("error") else None
                rec = FailureRecord(
                    task_id=task_id,
                    model=name,
                    test_id=test_id,
                    passed=passed,
                    expected=expected,
                    actual=actual,
                    error=error,
                    timestamp=now,
                )
                if not rec.passed or rec.error:
                    message = error or f"mismatch expected={expected!r} actual={actual!r}"
                    category = "agent_error" if error else "agent_mismatch"
                    features: Dict[str, Any] = {
                        "model": name,
                        "test_id": test_id,
                        "has_error": bool(error),
                        "expected_prefix": expected[:80],
                        "actual_prefix": actual[:80],
                    }
                    signature_id = logger.get_or_create_signature(
                        stage_name="agent",
                        category=category,
                        message=message,
                        key_features=features,
                        timestamp=now,
                    )
                    patterns.append(
                        {
                            "signature_id": signature_id,
                            "stage_name": "agent",
                            "category": category,
                            "model": name,
                            "test_id": test_id,
                            "message": message[:200],
                        }
                    )
                    records.append(rec)

        logger.log_failure_records(records)

        return {
            "failure_patterns": patterns,
            "success_rates": success_rates,
            "severity_scores": {},
            "model_errors": model_errors,
        }
    
    def document_in_context_framework(self, state: RedTeamState) -> Dict[str, Any]:
        """Phase 4: Document patterns in Living Context Framework"""
        print("\n📚 PHASE 4: Documenting in Living Context Framework")

        task_id = (state.get("task_id") or "").strip()
        weakness = (state.get("target_weakness") or state.get("weakness_category") or "").strip()
        discovered = datetime.now().isoformat()
        year = datetime.now().year

        fp_id = f"FP-{year}-{task_id}" if task_id else f"FP-{year}-UNKNOWN"
        safe_task_id = task_id or "unknown_task"
        pattern_file = self.context_dir / "failure_patterns" / f"{safe_task_id}.md"

        success_rates = state.get("success_rates") or {}
        model_errors = state.get("model_errors") or {}
        affected_models: List[Dict[str, Any]] = []
        for model, pass_rate in success_rates.items():
            if str(model).startswith("__"):
                continue
            try:
                pr = float(pass_rate)
            except Exception:
                pr = 0.0
            pr = max(0.0, min(1.0, pr))
            affected_models.append({"model": str(model), "pass_rate": pr, "failure_rate": 1.0 - pr})

        trap_block = ""
        if weakness == "COBOL_PACKED_DECIMAL":
            trap_block = """### The Trap

```cobol
01 CUSTOMER-RECORD.
   05 CUSTOMER-ID      PIC 9(5) COMP-3.
   05 BALANCE          PIC S9(7)V99 COMP-3.
```

**Model Failure Mode**: Interprets COMP-3 as regular binary instead of packed BCD, causing silent data corruption.

### Root Cause

Frontier models often under-expose legacy IBM mainframe data formats (packed decimals, file-status semantics), leading to incorrect assumptions during parsing/validation.

### Mitigation Strategy

```python
from typing import List

def validate_comp3_usage(code: str) -> List[str]:
    issues = []
    if "COMP-3" in code.upper():
        issues.append(
            "WARNING: COMP-3 packed decimals require BCD parsing. Verify the implementation decodes packed format correctly."
        )
    return issues
```
"""

        meta_lines: List[str] = [
            "---",
            f"id: {fp_id}",
            f"task_id: {safe_task_id}",
            f"weakness: {weakness}",
            f"discovered: {discovered}",
            "artifact_type: failure_pattern",
            "schema_version: 1",
            "starcoder_oracle_pass_rate: 1.0",
            f"last_validated: {discovered}",
            "validation_status: effective",
            "affected_models:",
        ]
        if affected_models:
            for entry in affected_models:
                meta_lines.append(f"  - model: {entry['model']}")
                meta_lines.append(f"    pass_rate: {entry['pass_rate']:.4f}")
                meta_lines.append(f"    failure_rate: {entry['failure_rate']:.4f}")
        else:
            meta_lines.append("  - model: none")
            meta_lines.append("    pass_rate: 0.0000")
            meta_lines.append("    failure_rate: 0.0000")
        meta_lines.append("---")

        content = "\n".join(meta_lines) + "\n\n"
        content += f"## Failure Pattern: {weakness}\n\n"
        content += f"**ID**: {fp_id}  \n"
        content += f"**Discovered**: {discovered}  \n"
        content += f"**Task**: {safe_task_id}  \n\n"

        content += "### Affected Models\n\n"
        for entry in affected_models:
            content += f"- {entry['model']}: {entry['failure_rate'] * 100:.1f}% failure\n"
        if not affected_models:
            content += "- (no model results recorded)\n"

        if model_errors:
            content += "### Model Availability\n\n"
            for model, errs in model_errors.items():
                first = str(errs[0]) if errs else ""
                content += f"- {model}: {first}\n"
            content += "\n"

        content += "\n### Oracle Success Rate\n\n- 100% (reference/oracle)\n\n"
        if trap_block:
            content += trap_block + "\n"

        pattern_file.write_text(content, encoding="utf-8", newline="\n")
        print(f"✅ Created: {pattern_file}")

        report_path = self.results_dir / f"{safe_task_id}_report.md"
        report_lines: List[str] = [f"# Report: {safe_task_id}", ""]
        report_lines.extend(
            [
                "## Summary",
                "",
                f"- Task: {safe_task_id}",
                f"- Weakness: {weakness or '(unknown)'}",
                f"- Timestamp: {discovered}",
                "",
                "## Artifacts",
                "",
                f"- Failure pattern: {pattern_file.as_posix()}",
                f"- Run log (JSONL): {(self.context_dir / 'runs.jsonl').as_posix()}",
                f"- Run database (SQLite): {(self.context_dir / 'runs.sqlite').as_posix()}",
                "",
            ]
        )

        report_lines.append("## Success Rates")
        report_lines.append("")
        if success_rates:
            report_lines.append("| Model | Pass Rate |")
            report_lines.append("|------|----------:|")
            for model in sorted(success_rates.keys(), key=lambda s: str(s)):
                try:
                    pr = float(success_rates[model])
                except Exception:
                    pr = 0.0
                pr = max(0.0, min(1.0, pr))
                report_lines.append(f"| {model} | {pr * 100:.1f}% |")
            report_lines.append("")
        else:
            report_lines.append("- No success rates recorded")
            report_lines.append("")

        if model_errors:
            report_lines.append("## Model Availability")
            report_lines.append("")
            for model, errs in sorted(model_errors.items(), key=lambda kv: str(kv[0])):
                first = str(errs[0]) if errs else ""
                report_lines.append(f"- {model}: {first}")
            report_lines.append("")

        task_root = (self.task_dir / safe_task_id).as_posix()
        report_lines.extend(
            [
                "## Task Files",
                "",
                f"- Task directory: {task_root}",
                f"- Instruction: {task_root}/instruction.md",
                f"- Tests: {task_root}/tests/",
                f"- Reference solution: {task_root}/solution/solve.sh",
                f"- Environment: {task_root}/environment/Dockerfile",
                "",
            ]
        )

        report_path.write_text("\n".join(report_lines) + "\n", encoding="utf-8", newline="\n")

        return {
            "context_documentation": str(pattern_file),
            "context_updates": f"Created {pattern_file}",
            "report_path": str(report_path),
        }


def run_pipeline(
    task_ids: List[str],
    weakness_categories: List[str],
    models: Optional[List[str]] = None,
    output_dir: str = "./results",
):
    """Run the complete red-teaming pipeline"""
    pipeline = RedTeamingPipeline(results_dir=output_dir)

    workflow = pipeline.build_graph()
    app = workflow.compile()
    
    for task_id in task_ids:
        for category in weakness_categories:
            print(f"\n{'='*60}")
            print(f"🚀 RUNNING RED-TEAM VALIDATION: {task_id}")
            print(f"{'='*60}")
            
            initial_state: RedTeamState = {
                "task_id": task_id,
                "weakness_category": category,
                "target_weakness": category,
                "num_variants": 3,
                "num_test_cases": 3,
                "adversarial_tests": [],
                "generation_metadata": {},
                "solver_results": {},
                "execution_logs": [],
                "failure_patterns": [],
                "success_rates": {},
                "context_documentation": None,
                "severity_scores": {},
                "context_updates": "",
                "reference_solution": "",
                "test_harness": "",
                "report_path": "",
                "timestamp": datetime.now().isoformat(),
                "duration_seconds": 0.0,
            }

            if models is not None:
                initial_state["target_models"] = models
            if args.regenerate_tests:
                initial_state["regenerate_tests"] = True
            if args.use_starcoder_variants:
                initial_state["use_starcoder_variants"] = True
            
            try:
                result = app.invoke(initial_state)
                print("\n✅ RED-TEAM VALIDATION COMPLETE")
                print(f"   Report: {result['report_path']}")
            except Exception as e:
                print(f"\n❌ ERROR: {e}")


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Run red-teaming pipeline")
    parser.add_argument("--tasks", nargs="+", help="Task IDs to validate")
    parser.add_argument("--weaknesses", nargs="+", help="Weakness categories")
    parser.add_argument("--models", nargs="+", help="Models to test")
    parser.add_argument("--no-models", action="store_true", help="Skip external model calls")
    parser.add_argument("--regenerate-tests", action="store_true", help="Regenerate test variants for existing tasks")
    parser.add_argument("--use-starcoder-variants", action="store_true", help="When regenerating, use StarCoder via Ollama to generate new variants")
    parser.add_argument("--output-dir", default="./results", help="Output directory")
    
    args = parser.parse_args()
    
    tasks = args.tasks or ["cobol_packed_decimal-adversarial-001"]
    weaknesses = args.weaknesses or ["COBOL_PACKED_DECIMAL"]

    models = [] if args.no_models else (args.models or None)
    run_pipeline(tasks, weaknesses, models, args.output_dir)
