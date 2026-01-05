from __future__ import annotations

from pathlib import Path

from harbor.models.task.config import TaskConfig

from .spec import TaskSpec


def _render_task_toml(spec: TaskSpec) -> str:
    cfg = TaskConfig(
        version="2.0",
        metadata=spec.to_metadata_dict(),
    )
    return cfg.model_dump_toml()


def _render_dockerfile(spec: TaskSpec) -> str:
    lines: list[str] = [f"FROM {spec.environment.base_image}", ""]

    if spec.environment.apt_packages:
        apt_items = " ".join(p.name for p in spec.environment.apt_packages)
        lines.append("RUN apt-get update \\")
        lines.append(f"    && apt-get install -y {apt_items} \\")
        lines.append("    && rm -rf /var/lib/apt/lists/*")
        lines.append("")

    if spec.environment.pip_packages:
        lines.append("RUN python3 -m pip install --upgrade pip")
        req_lines = [f"{p.name}=={p.version}" for p in spec.environment.pip_packages]
        joined = " \\\n    ".join(req_lines)
        lines.append("RUN python3 -m pip install \\")
        lines.append(f"    {joined}")
        lines.append("")

    if spec.environment.workdir:
        lines.append(f"WORKDIR {spec.environment.workdir}")
        lines.append("")

    lines.extend(
        [
            "COPY solution/ /solution/",
            "COPY tests/ /tests/",
            "RUN chmod +x /solution/solve.sh /tests/test.sh || true",
            'CMD ["/bin/bash"]',
            "",
        ]
    )
    return "\n".join(lines)


def _render_solve_sh(spec: TaskSpec) -> str:
    reward = spec.reward_path
    input_path = spec.inputs[0].path if spec.inputs else "/tests/input.txt"
    lines: list[str] = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        f"mkdir -p \"$(dirname {reward})\"",
        f"python3 /tests/test_outputs.py --oracle --input \"{input_path}\" --output \"{reward}\"",
        "cat \"$1\" >/dev/null 2>&1 || true",
        "",
    ]
    return "\n".join(lines)


def _render_test_outputs_py(spec: TaskSpec) -> str:
    reward = spec.reward_path
    lines: list[str] = [
        "import argparse",
        "import sys",
        "from pathlib import Path",
        "",
        spec.oracle.verifier_python.rstrip(),
        "",
        "def main() -> int:",
        "    parser = argparse.ArgumentParser()",
        "    parser.add_argument('--oracle', action='store_true')",
        "    parser.add_argument('--input', required=True)",
        "    parser.add_argument('--output', required=True)",
        "    args = parser.parse_args()",
        "    inp = Path(args.input).read_text(encoding='utf-8', errors='replace')",
        "    out_path = Path(args.output)",
        "    out_path.parent.mkdir(parents=True, exist_ok=True)",
        "    if args.oracle:",
        "        result = oracle_solve(inp)",
        "        out_path.write_text(result + '\\n', encoding='utf-8', newline='\\n')",
        "        return 0",
        "    if not out_path.exists():",
        f"        sys.stderr.write('Missing reward file: {reward}\\n')",
        "        return 2",
        "    candidate = out_path.read_text(encoding='utf-8', errors='replace').strip()",
        "    expected = oracle_solve(inp).strip()",
        "    if candidate != expected:",
        "        sys.stderr.write('Output mismatch\\n')",
        "        sys.stderr.write(f'expected={expected!r} actual={candidate!r}\\n')",
        "        return 1",
        "    return 0",
        "",
        "if __name__ == '__main__':",
        "    raise SystemExit(main())",
        "",
    ]
    return "\n".join(lines)


def _render_test_sh(spec: TaskSpec) -> str:
    reward = spec.reward_path
    input_path = spec.inputs[0].path if spec.inputs else "/tests/input.txt"
    lines: list[str] = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        f"mkdir -p \"$(dirname {reward})\"",
        f"bash /solution/solve.sh \"{input_path}\" >\"{reward}\" 2>/dev/null || true",
        f"python3 /tests/test_outputs.py --input \"{input_path}\" --output \"{reward}\"",
        "",
    ]
    return "\n".join(lines)


def render_task(spec: TaskSpec, task_dir: Path | str) -> Path:
    task_dir = Path(task_dir)
    task_dir.mkdir(parents=True, exist_ok=True)

    (task_dir / "environment").mkdir(parents=True, exist_ok=True)
    (task_dir / "solution").mkdir(parents=True, exist_ok=True)
    (task_dir / "tests").mkdir(parents=True, exist_ok=True)

    (task_dir / "instruction.md").write_text(spec.instruction_md.rstrip() + "\n", encoding="utf-8", newline="\n")
    (task_dir / "task.toml").write_text(_render_task_toml(spec), encoding="utf-8", newline="\n")
    (task_dir / "environment" / "Dockerfile").write_text(_render_dockerfile(spec), encoding="utf-8", newline="\n")
    (task_dir / "solution" / "solve.sh").write_text(_render_solve_sh(spec), encoding="utf-8", newline="\n")
    (task_dir / "tests" / "test.sh").write_text(_render_test_sh(spec), encoding="utf-8", newline="\n")
    (task_dir / "tests" / "test_outputs.py").write_text(_render_test_outputs_py(spec), encoding="utf-8", newline="\n")

    return task_dir
