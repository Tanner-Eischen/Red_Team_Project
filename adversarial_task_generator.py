#!/usr/bin/env python3
"""
Adversarial Task Generator
Generate standardized adversarial tasks that expose AI model weaknesses
"""

import argparse
import json
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from harbor_renderer import TaskSpec, render_task
from harbor_renderer.spec import (
    AntiCheatingSpec,
    EnvironmentSpec,
    IOSpec,
    MetadataSpec,
    OracleStrategy,
    PackageSpec,
    TestCaseSpec,
    TestPlanSpec,
)


class AdversarialTaskGenerator:
    """Generate reproducible adversarial test cases"""
    
    def __init__(self, output_dir: str = "./adversarial_tasks"):
        """Initialize generator"""
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._use_starcoder_variants = False
        self._cached_problem_generator: Optional[Dict[str, Any]] = None

    def _load_problem_generator_config(self) -> Dict[str, Any]:
        if self._cached_problem_generator is not None:
            return self._cached_problem_generator

        default = {
            "provider": "ollama",
            "model": "starcoder2:instruct",
            "host": "http://localhost:11434",
            "temperature": 0.3,
            "max_tokens": 512,
            "timeout_seconds": 180,
        }

        config_path = Path(__file__).resolve().parent / ".cline" / "config.json"
        if not config_path.exists():
            self._cached_problem_generator = default
            return default

        try:
            cfg = json.loads(config_path.read_text(encoding="utf-8"))
            adv = cfg.get("adversarial_testing") or cfg.get("adversarial_mode") or {}
            pg = adv.get("problem_generator", {})
            merged = {
                **default,
                **{k: v for k, v in pg.items() if v is not None},
            }
            self._cached_problem_generator = merged
            return merged
        except Exception:
            self._cached_problem_generator = default
            return default

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

    def _ollama_generate(self, prompt: str) -> str:
        pg = self._load_problem_generator_config()
        host = str(pg.get("host") or "http://localhost:11434").rstrip("/")
        url = f"{host}/api/generate"
        model = str(pg.get("model") or "").strip()
        if model.startswith("ollama/"):
            model = model.split("/", 1)[1].strip()
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": pg.get("temperature", 0.3),
                "num_predict": pg.get("max_tokens", 1024),
            },
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
        timeout = float(pg.get("timeout_seconds", 60))
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
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
                    with urllib.request.urlopen(req, timeout=timeout) as resp:
                        raw = resp.read().decode("utf-8", errors="replace")
                else:
                    raise
            else:
                raise
        parsed = json.loads(raw)
        return str(parsed.get("response") or "")

    def _ollama_generate_json(self, prompt: str) -> Optional[Any]:
        try:
            raw = self._ollama_generate(prompt).strip()
            candidates: List[str] = []
            if "```" in raw:
                parts = [p for p in raw.split("```") if p.strip()]
                candidates.extend([p.strip() for p in parts])
            candidates.append(raw)

            for cand in candidates:
                text = cand.strip()
                lowered = text.lower()
                if lowered.startswith("json"):
                    text = text[4:].strip()
                if lowered.startswith("python"):
                    text = text[6:].strip()

                try:
                    return json.loads(text)
                except Exception:
                    extracted = self._extract_json_value(text)
                    if extracted is not None:
                        return extracted
        except Exception:
            return None

    def _extract_json_value(self, text: str) -> Optional[Any]:
        s = (text or "").strip()
        if not s:
            return None

        start = None
        for i, ch in enumerate(s):
            if ch in "{[":
                start = i
                break
        if start is None:
            return None

        opener = s[start]
        closer = "}" if opener == "{" else "]"
        depth = 0
        in_str = False
        escape = False

        for j in range(start, len(s)):
            ch = s[j]
            if in_str:
                if escape:
                    escape = False
                    continue
                if ch == "\\":
                    escape = True
                    continue
                if ch == '"':
                    in_str = False
                continue

            if ch == '"':
                in_str = True
                continue

            if ch == opener:
                depth += 1
                continue

            if ch == closer:
                depth -= 1
                if depth == 0:
                    candidate = s[start : j + 1]
                    try:
                        return json.loads(candidate)
                    except Exception:
                        return None

        return None

    def profile_cobol_weaknesses(self, target_model: str, n_snippets: int = 10) -> Dict[str, Any]:
        prompt = (
            "Generate a JSON object with keys: target_model, weakness_taxonomy, probes. "
            "weakness_taxonomy must be a list of short categories describing where the target model is likely to fail in COBOL. "
            "probes must be a list of exactly "
            f"{n_snippets} "
            "items; each item is an object with keys: topic, cobol_snippet, expected_failure_mode. "
            "The topics must cover: (1) COMP-3 packed decimal arithmetic, (2) OCCURS DEPENDING ON clauses, "
            "(3) FILE STATUS error handling, (4) EVALUATE statement nuances. "
            "Include subtle edge cases that could cause silent data corruption. Output JSON only. "
            f"Target model: {target_model}"
        )
        generated = self._ollama_generate_json(prompt)
        if isinstance(generated, dict):
            return generated
        return {
            "target_model": target_model,
            "weakness_taxonomy": [],
            "probes": [],
            "error": "Failed to generate JSON via Ollama",
        }

    def _decode_comp3_hex(self, value: str) -> Optional[str]:
        cleaned = "".join(ch for ch in value.lower() if ch in "0123456789abcdef")
        if not cleaned or len(cleaned) % 2 != 0:
            return None
        try:
            data = bytes.fromhex(cleaned)
        except ValueError:
            return None
        if not data:
            return None
        digits: List[int] = []
        for b in data[:-1]:
            hi = (b >> 4) & 0xF
            lo = b & 0xF
            if hi > 9 or lo > 9:
                return None
            digits.append(hi)
            digits.append(lo)
        last = data[-1]
        last_hi = (last >> 4) & 0xF
        last_sign = last & 0xF
        if last_hi > 9:
            return None
        digits.append(last_hi)
        if last_sign in (0xC, 0xF):
            sign = 1
        elif last_sign == 0xD:
            sign = -1
        else:
            return None
        number = "".join(str(d) for d in digits).lstrip("0") or "0"
        if number == "0":
            sign = 1
        return number if sign > 0 else f"-{number}"

    def _comp3_variant_inputs(self) -> List[str]:
        fallback = [
            "123C",
            "00012D",
            "12345C",
            "987F",
            "0x12 34 5c",
            "12AC",
            "123E",
            "000D",
        ]

        if not self._use_starcoder_variants:
            return fallback

        prompt = (
            "Return a JSON array of 8 strings. Each string is a single-line hex input representing a COBOL COMP-3 packed-decimal. "
            "Mix valid and invalid cases. Include: C/D/F sign variants, whitespace-separated bytes, optional 0x prefix, "
            "leading zeros, odd digit counts, and at least two invalid cases (bad digit nibble or bad sign nibble). "
            "Output JSON only. No markdown, no code fences, no explanations."
        )
        generated: Optional[Any] = None
        for _ in range(3):
            generated = self._ollama_generate_json(prompt)
            if isinstance(generated, dict):
                for v in generated.values():
                    if isinstance(v, list) and all(isinstance(x, str) for x in v) and len(v) >= 3:
                        return v[:8]
            if isinstance(generated, list) and all(isinstance(x, str) for x in generated) and len(generated) >= 3:
                return generated[:8]
        return fallback

    def _cobol_oracle_verifier(self) -> str:
        return (
            "def oracle_solve(value: str) -> str:\n"
            "    value = value or \"\"\n"
            "    cleaned = \"\".join(ch for ch in value.lower() if ch in \"0123456789abcdef\")\n"
            "    if not cleaned or len(cleaned) % 2 != 0:\n"
            "        return \"ERROR\"\n"
            "    try:\n"
            "        data = bytes.fromhex(cleaned)\n"
            "    except ValueError:\n"
            "        return \"ERROR\"\n"
            "    if not data:\n"
            "        return \"ERROR\"\n"
            "    digits = []\n"
            "    for b in data[:-1]:\n"
            "        hi = (b >> 4) & 0xF\n"
            "        lo = b & 0xF\n"
            "        if hi > 9 or lo > 9:\n"
            "            return \"ERROR\"\n"
            "        digits.append(hi)\n"
            "        digits.append(lo)\n"
            "    last = data[-1]\n"
            "    last_hi = (last >> 4) & 0xF\n"
            "    last_sign = last & 0xF\n"
            "    if last_hi > 9:\n"
            "        return \"ERROR\"\n"
            "    digits.append(last_hi)\n"
            "    if last_sign in (0xC, 0xF):\n"
            "        sign = 1\n"
            "    elif last_sign == 0xD:\n"
            "        sign = -1\n"
            "    else:\n"
            "        return \"ERROR\"\n"
            "    number = \"\".join(str(d) for d in digits).lstrip(\"0\") or \"0\"\n"
            "    if number == \"0\":\n"
            "        sign = 1\n"
            "    return number if sign > 0 else f\"-{number}\"\n"
        )

    def generate_task(
        self,
        task_id: str,
        weakness_category: str,
        target_models: List[str],
        num_variants: int = 3,
        description: str = "",
        use_starcoder: bool = False
    ) -> Dict:
        """Generate the retained legacy COBOL demonstration.

        Other categories previously returned echo tasks, which could be mistaken
        for implemented benchmarks. Scientific families live in ``redforge``.
        """
        if weakness_category != "COBOL_PACKED_DECIMAL":
            raise ValueError(
                f"Unimplemented legacy weakness category: {weakness_category}. "
                "Use redforge task generate with an implemented scientific family."
            )
        if num_variants < 1:
            raise ValueError("num_variants must be positive")
        self._use_starcoder_variants = bool(use_starcoder)
        task_path = self.output_dir / task_id
        task_path.mkdir(parents=True, exist_ok=True)

        metadata = MetadataSpec(
            category=weakness_category,
            difficulty="very_hard" if weakness_category == "COBOL_PACKED_DECIMAL" else "hard",
            tags=[weakness_category.lower(), "adversarial", "codegen"],
        )

        env = EnvironmentSpec(
            base_image="ubuntu:22.04",
            apt_packages=[
                PackageSpec(name="bash", version="latest"),
                PackageSpec(name="curl", version="latest"),
                PackageSpec(name="git", version="latest"),
                PackageSpec(name="python3", version="latest"),
            ],
            pip_packages=[],
            workdir="/app",
        )

        reward_path = "/logs/verifier/reward.txt"
        artifact_path = "/app/output/result.txt"

        inputs = [
            IOSpec(
                path="/app/input/variant_1.input",
                description=f"Primary adversarial input for {weakness_category}",
            )
        ]
        outputs = [
            IOSpec(
                path=artifact_path,
                description="Agent-produced decoded integer; distinct from verifier reward",
            )
        ]

        if weakness_category == "COBOL_PACKED_DECIMAL":
            oracle_verifier = self._cobol_oracle_verifier()
            oracle_steps = [
                "Normalize hex input representation",
                "Decode packed decimal nibbles into digits",
                "Apply sign nibble semantics C/F positive, D negative",
                "Return canonical signed integer or ERROR",
            ]
        else:
            oracle_verifier = (
                "def oracle_solve(value: str) -> str:\n"
                "    value = value or \"\"\n"
                "    return value\n"
            )
            oracle_steps = [
                "Read raw input",
                "Return input unchanged as expected output",
            ]

        oracle = OracleStrategy(steps=oracle_steps, verifier_python=oracle_verifier)

        if weakness_category == "COBOL_PACKED_DECIMAL":
            requirements = [
                "Decode valid COMP-3 values to correct signed integers",
                "Reject invalid digit or sign nibbles with ERROR",
                "Treat negative zero as unsigned zero",
            ]
        else:
            requirements = [
                f"Handle representative inputs for {weakness_category} without runtime errors",
            ]

        cases: List[TestCaseSpec] = []
        for i in range(1, num_variants + 1):
            case_req_ids = [requirements[0]] if requirements else []
            cases.append(
                TestCaseSpec(
                    id=f"variant_{i}",
                    requirement_ids=case_req_ids,
                    input_path=f"/tests/variant_{i}.input",
                    description=f"Adversarial variant {i} for {weakness_category}",
                )
            )

        test_plan = TestPlanSpec(requirements=requirements, cases=cases)

        anti_cheating = AntiCheatingSpec(
            constraints=[
                "Do not special-case known test inputs",
                "Do not ignore packed-decimal rules even if outputs match examples",
                "Do not rely on prompt text to infer expected outputs",
            ]
        )

        instruction = self._create_instruction(weakness_category, description)
        instruction += (
            "\nLegacy migration demonstration: read /app/input/variant_1.input and write "
            "the decoded value to /app/output/result.txt. This fixed public example "
            "does not establish held-out benchmark validity.\n"
        )

        spec = TaskSpec(
            task_id=task_id,
            title=f"Adversarial Task: {weakness_category}",
            instruction_md=instruction,
            metadata=metadata,
            inputs=inputs,
            outputs=outputs,
            reward_path=reward_path,
            environment=env,
            oracle=oracle,
            test_plan=test_plan,
            anti_cheating=anti_cheating,
            public_files={"input/variant_1.input": "123C\n"},
            # This fixture expectation is authored explicitly, not obtained by
            # executing the reference implementation inside the verifier.
            expected_outputs={artifact_path: "123"},
        )

        render_task(spec, task_path)

        for i in range(1, num_variants + 1):
            self._create_test_variant(task_path, i, weakness_category)

        report = self._create_report_template(task_id, weakness_category, target_models)
        (task_path / "VALIDATION_REPORT.md").write_text(report, encoding="utf-8", newline="\n")
        generation_meta = {
            "task_id": task_id,
            "path": str(task_path),
            "weakness_category": weakness_category,
            "num_variants": num_variants,
            "created": datetime.now().isoformat(),
            "status": "unverified_legacy",
            "target_models": list(target_models),
            "generator": "AdversarialTaskGenerator",
        }
        (task_path / "generation_metadata.json").write_text(
            json.dumps(generation_meta, indent=2),
            encoding="utf-8",
            newline="\n",
        )
        return {
            **generation_meta
        }
    
    def _create_instruction(self, weakness: str, description: str) -> str:
        """Create instruction.md"""
        if weakness == "COBOL_PACKED_DECIMAL":
            return """# Adversarial Task: COBOL_PACKED_DECIMAL

## Problem Statement

Decode a COBOL COMP-3 / packed-decimal value provided as a hex string and print the corresponding signed integer in canonical decimal form.

## Input

A single line string representing packed-decimal bytes, in hex. The input may include:
- Upper/lowercase hex
- Optional `0x` prefix
- Optional whitespace separators between bytes

All non-hex characters are ignored for the purposes of decoding, except that the resulting hex sequence must have even length (whole bytes).

## Packed Decimal Rules

- Each nibble (4 bits) encodes one decimal digit `0`–`9`, except the final nibble.
- The final nibble is a sign nibble:
  - `C` or `F` = non-negative
  - `D` = negative
- The high nibble of the last byte is the final digit.

If any digit nibble is not in `0`–`9`, or if the sign nibble is not one of `C/D/F`, output `ERROR`.

## Output

Print exactly one line:
- The decoded signed integer in canonical decimal form (no leading zeros), or
- `ERROR`

`-0` must be printed as `0`.
"""
        return f"""# Adversarial Task: {weakness}

## Problem Statement

{description or f"Task designed to expose weakness in {weakness} handling"}

## Weakness Category

**{weakness}**

## Expected Behavior

Reference implementation must pass all tests:
- variant_1: Basic test
- variant_2: Edge case
- variant_3: Complex scenario

## Success Criteria

✅ All tests pass
✅ No runtime errors
✅ Output matches expected exactly
"""
    
    def _create_task_config(
        self,
        task_id: str,
        weakness: str,
        models: List[str],
        num_variants: int
    ) -> Dict:
        """Create task.yaml configuration"""
        return {
            "task": {
                "id": task_id,
                "weakness_category": weakness,
                "created": datetime.now().isoformat(),
                "version": "1.0"
            },
            "metadata": {
                "description": f"Adversarial test for {weakness}",
                "target_models": models,
                "expected_failure_rate": 0.70,
                "difficulty": "high"
            },
            "test": {
                "num_variants": num_variants,
                "timeout_seconds": 30,
                "require_exact_match": True
            },
            "environment": {
                "dockerfile": "environment/Dockerfile",
                "working_dir": "/app"
            },
            "solution": {
                "reference": "solution/solve.sh",
                "language": "shell/bash"
            }
        }

    def _yaml_scalar(self, value: Any) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        if value is None:
            return "null"
        if isinstance(value, (int, float)):
            return str(value)
        s = str(value)
        s = s.replace("\\", "\\\\").replace('"', '\\"')
        return f"\"{s}\""

    def _yaml_dump(self, value: Any, indent: int = 0) -> str:
        prefix = "  " * indent
        if isinstance(value, dict):
            lines: List[str] = []
            for k, v in value.items():
                key = str(k)
                if isinstance(v, (dict, list)):
                    lines.append(f"{prefix}{key}:")
                    lines.append(self._yaml_dump(v, indent + 1))
                else:
                    lines.append(f"{prefix}{key}: {self._yaml_scalar(v)}")
            return "\n".join(lines)
        if isinstance(value, list):
            lines = []
            for item in value:
                if isinstance(item, (dict, list)):
                    lines.append(f"{prefix}-")
                    lines.append(self._yaml_dump(item, indent + 1))
                else:
                    lines.append(f"{prefix}- {self._yaml_scalar(item)}")
            return "\n".join(lines)
        return f"{prefix}{self._yaml_scalar(value)}"
    
    def _create_test_variant(self, task_path: Path, variant_num: int, weakness: str):
        """Create test variant files"""
        if weakness == "COBOL_PACKED_DECIMAL":
            inputs = self._comp3_variant_inputs()
            input_content = inputs[(variant_num - 1) % len(inputs)]
            decoded = self._decode_comp3_hex(input_content)
            expected_content = decoded if decoded is not None else "ERROR"
            (task_path / "tests" / f"variant_{variant_num}.input").write_text(input_content, encoding="utf-8", newline="\n")
            (task_path / "tests" / f"variant_{variant_num}.expected").write_text(expected_content, encoding="utf-8", newline="\n")
            return
        raise ValueError(f"No implemented test family for legacy weakness {weakness!r}")
    
    def _create_report_template(self, task_id: str, weakness: str, models: List[str]) -> str:
        """Create validation report template"""
        model_list = "\n".join([f"- {model}" for model in models])
        return f"""# Validation Report: {task_id}

**Task**: {weakness}
**Generated**: {datetime.now().isoformat()}

## Test Results

### Models Tested
{model_list}

### Success Rates

| Model | Pass Rate |
|-------|-----------|
| (Model 1) | % |
| (Model 2) | % |
| (Model 3) | % |
"""


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(description="Generate adversarial AI tasks")
    parser.add_argument("--weakness", help="Weakness category (e.g., COBOL_PACKED_DECIMAL)")
    parser.add_argument("--num-tasks", type=int, default=1, help="Number of tasks to generate")
    parser.add_argument("--output-dir", default="./adversarial_tasks", help="Output directory for tasks")
    parser.add_argument("--use-starcoder", action="store_true", help="Use StarCoder via Ollama to generate adversarial variants")

    parser.add_argument("--profile-model", help="If set, generate a COBOL weakness taxonomy/probes for this target model and exit")
    parser.add_argument("--profile-out", default="./.context/model_profiles", help="Output directory for model profiles")
    parser.add_argument("--profile-n", type=int, default=10, help="Number of probe snippets to generate")

    args = parser.parse_args()

    generator = AdversarialTaskGenerator(args.output_dir)

    if args.profile_model:
        out_dir = Path(args.profile_out)
        out_dir.mkdir(parents=True, exist_ok=True)
        profile = generator.profile_cobol_weaknesses(args.profile_model, n_snippets=args.profile_n)
        out_path = out_dir / f"{args.profile_model}.json"
        out_path.write_text(json.dumps(profile, indent=2), encoding="utf-8", newline="\n")
        print(f"✅ Wrote profile: {out_path}")
        return

    if not args.weakness:
        raise SystemExit("--weakness is required unless --profile-model is provided")

    print(f"🚀 Generating {args.num_tasks} tasks for {args.weakness}...")
    print("")

    for i in range(1, args.num_tasks + 1):
        task_id = f"{args.weakness.lower()}-adversarial-{i:03d}"
        result = generator.generate_task(
            task_id=task_id,
            weakness_category=args.weakness,
            target_models=["gpt-4o", "claude-3.5-sonnet", "llama-3.1-405b"],
            num_variants=3,
            description=f"Adversarial test {i} for {args.weakness}",
            use_starcoder=args.use_starcoder
        )

        print(f"✅ Generated: {result['task_id']}")
        print(f"   Path: {result['path']}")

    print("")
    print(f"✅ Complete! Generated {args.num_tasks} tasks")
    print(f"   Output directory: {args.output_dir}")


if __name__ == "__main__":
    main()
