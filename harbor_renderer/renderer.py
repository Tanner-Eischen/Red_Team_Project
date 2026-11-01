"""Conservative migration renderer for explicit legacy artifact contracts.

The deterministic scientific task-family registry is the preferred authoring
path. Legacy specs without independent expected outputs fail closed.
"""

from __future__ import annotations

import json
import shlex
from pathlib import Path, PurePosixPath

from .spec import TaskSpec


def _render_task_toml(spec: TaskSpec) -> str:
    artifacts = json.dumps([output.path for output in spec.outputs])
    return (
        'schema_version = "1.3"\n'
        f"artifacts = {artifacts}\n\n"
        "[metadata]\n"
        f"task_id = {json.dumps(spec.task_id)}\n"
        'validation_status = "unverified_legacy"\n\n'
        '[agent]\ntimeout_sec = 600\nuser = "agent"\nnetwork_mode = "no-network"\n\n'
        '[verifier]\ntimeout_sec = 120\nuser = "root"\n'
        'environment_mode = "separate"\nnetwork_mode = "no-network"\n\n'
        '[environment]\ncpus = 1\nmemory_mb = 2048\nnetwork_mode = "no-network"\n'
    )


def _render_dockerfile(spec: TaskSpec) -> str:
    lines = [f"FROM {spec.environment.base_image}"]
    if spec.environment.apt_packages:
        packages = " ".join(shlex.quote(p.name if p.version == "latest" else f"{p.name}={p.version}")
                            for p in spec.environment.apt_packages)
        lines.append(f"RUN apt-get update && apt-get install -y --no-install-recommends {packages} && rm -rf /var/lib/apt/lists/*")
    if spec.environment.pip_packages:
        packages = " ".join(shlex.quote(f"{p.name}=={p.version}") for p in spec.environment.pip_packages)
        lines.append(f"RUN python3 -m pip install --no-cache-dir {packages}")
    lines.extend(["RUN useradd --create-home --uid 1000 agent", "WORKDIR /app", "COPY public/ /app/",
                  "RUN chown -R agent:agent /app", "USER agent", 'CMD ["/bin/bash"]', ""])
    return "\n".join(lines)


def _render_solve_py(spec: TaskSpec) -> str:
    return (
        "from pathlib import Path\n" + spec.oracle.verifier_python.rstrip() + "\n"
        f"input_path = Path({spec.inputs[0].path!r})\n"
        f"output_path = Path({spec.outputs[0].path!r})\n"
        "output_path.parent.mkdir(parents=True, exist_ok=True)\n"
        "output_path.write_text(oracle_solve(input_path.read_text(encoding='utf-8')) + '\\n', encoding='utf-8')\n"
    )


def _render_verify_py(spec: TaskSpec) -> str:
    return (
        "import json\nfrom pathlib import Path\n"
        "expected = json.loads(Path('/tests/expected.json').read_text(encoding='utf-8'))\n"
        "def main():\n"
        "    for name, answer in expected.items():\n"
        "        path = Path(name)\n"
        "        if not path.is_file() or path.is_symlink():\n"
        "            return 1\n"
        "        if path.stat().st_size > 1048576:\n"
        "            return 1\n"
        "        if path.read_text(encoding='utf-8', errors='replace').strip() != answer.strip():\n"
        "            return 1\n"
        "    return 0\n"
        "if __name__ == '__main__':\n"
        "    raise SystemExit(main())\n"
    )


def _render_test_sh(spec: TaskSpec) -> str:
    return (
        "#!/usr/bin/env bash\nset -uo pipefail\n"
        "mkdir -p /logs/verifier\nrm -f /logs/verifier/reward.txt /logs/verifier/reward.json\n"
        "python3 /tests/verify.py\nstatus=$?\n"
        "case $status in\n"
        "  0) printf '1\\n' > /logs/verifier/reward.txt ;;\n"
        "  1) printf '0\\n' > /logs/verifier/reward.txt ;;\n"
        "  *) exit \"$status\" ;;\nesac\n"
    )


def render_task(spec: TaskSpec, task_dir: Path | str) -> Path:
    if not spec.public_files or not spec.expected_outputs:
        raise ValueError("Legacy specs require explicit public_files and independently authored expected_outputs; use redforge task generate for validated families")
    if spec.reward_path != "/logs/verifier/reward.txt":
        raise ValueError("reward_path must be /logs/verifier/reward.txt; solver artifacts belong in outputs")
    if len(spec.inputs) != 1 or len(spec.outputs) != 1:
        raise ValueError("Legacy migration renderer supports one explicit input and output only")
    for artifact in [*spec.inputs, *spec.outputs]:
        path = PurePosixPath(artifact.path)
        if not path.is_relative_to("/app") or ".." in path.parts:
            raise ValueError("Public input and output paths must remain under /app")
    input_relative = str(PurePosixPath(spec.inputs[0].path).relative_to("/app"))
    if input_relative not in spec.public_files:
        raise ValueError("input fixture is missing from public_files")
    if set(spec.expected_outputs) != {spec.outputs[0].path}:
        raise ValueError("expected_outputs must independently specify the declared output artifact")
    task_dir = Path(task_dir)
    if task_dir.exists() and any(task_dir.iterdir()):
        raise FileExistsError("Task packages are immutable; choose a new task directory")
    for relative in ("environment/public", "solution", "tests"):
        (task_dir / relative).mkdir(parents=True, exist_ok=True)
    files = {
        "instruction.md": spec.instruction_md.rstrip() + "\n",
        "task.toml": _render_task_toml(spec),
        "environment/Dockerfile": _render_dockerfile(spec),
        "solution/solve.sh": "#!/usr/bin/env bash\nset -euo pipefail\npython3 /solution/solve.py\n",
        "solution/solve.py": _render_solve_py(spec),
        "tests/test.sh": _render_test_sh(spec),
        "tests/verify.py": _render_verify_py(spec),
        "tests/expected.json": json.dumps(spec.expected_outputs, sort_keys=True),
    }
    files.update({f"environment/public/{name}": content for name, content in spec.public_files.items()})
    for relative, content in files.items():
        path = task_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
    return task_dir
