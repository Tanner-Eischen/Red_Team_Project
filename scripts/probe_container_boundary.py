"""Actual adversarial Docker probes. Uses author controls only; never model APIs."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import shutil

from harborforge.adapters.harbor_adapter import HarborAdapter
from harborforge.task_families.registry import _installer, generate_task
from harborforge.task_families import scientific_etl


PROBE = r'''
import atexit,os,resource,socket,time
from pathlib import Path
assert os.geteuid() in (1000,65534)
assert 'NoNewPrivs:\t1' in Path('/proc/self/status').read_text()
assert not Path('/var/run/docker.sock').exists()
for target in ('/tests/cases.json','/logs/verifier/reward.txt'):
 try:
  Path(target).read_bytes()
 except (PermissionError,FileNotFoundError): pass
 else: raise AssertionError('Protected read succeeded')
try:
 Path('/logs/verifier/reward.txt').write_text('1\n')
except (PermissionError,FileNotFoundError): pass
else: raise AssertionError('Forged reward write succeeded')
try:
 socket.create_connection(('1.1.1.1',443),timeout=.2)
except OSError: pass
else: raise AssertionError('External network reachable')
if os.geteuid()==65534:
 assert resource.getrlimit(resource.RLIMIT_AS)==(1073741824,1073741824)
 assert resource.getrlimit(resource.RLIMIT_CPU)==(15,15)
 assert resource.getrlimit(resource.RLIMIT_NPROC)==(16,16)
 assert resource.getrlimit(resource.RLIMIT_FSIZE)==(8388608,8388608)
 assert 'CapEff:\t0000000000000000' in Path('/proc/self/status').read_text()
 try: Path('/proc/'+str(os.getppid())+'/environ').read_bytes()
 except PermissionError: pass
 else: raise AssertionError('Judge environment readable')
 try: os.setuid(0)
 except PermissionError: pass
 else: raise AssertionError('Privilege escalation succeeded')
 marker=Path('/tmp/hf-detached-survived')
 assert not marker.exists()
 @atexit.register
 def evidence():
  Path('/tmp/hf-worker-proof.json').write_text('{"uid":65534,"protected_reads_denied":true,"reward_write_denied":true,"network_denied":true,"judge_environment_denied":true,"capabilities_zero":true,"limits_checked":true}')
 child=os.fork()
 if child==0:
  os.setsid()
  grandchild=os.fork()
  if grandchild: os._exit(0)
  time.sleep(.4)
  marker.write_text('escaped')
  os._exit(0)
 os.waitpid(child,0)
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--probe", action="append", help="Run only a named probe; default runs all eight")
    args = parser.parse_args()
    root = args.output_dir.resolve()
    root.mkdir(parents=True, exist_ok=False)
    baseline = generate_task("scientific-etl", 440, root / "source")
    good = _installer(scientific_etl.SOURCE, "input/observations.csv")
    forged = PROBE.split("if os.geteuid()==65534:")[0] + scientific_etl.BUGGY
    scripts = {
        "protected-worker-double-fork": (_installer(PROBE + scientific_etl.SOURCE, "input/observations.csv"), {"PASS"}),
        "forged-reward-rejected": (_installer(forged, "input/observations.csv"), {"MODEL_FAIL"}),
        "symlink-submission": (good.replace("raise SystemExit(result.returncode)",
            "target.unlink(); target.symlink_to('/etc/passwd')"), {"MODEL_FAIL"}),
        "hardlinked-output": (good.replace("raise SystemExit(result.returncode)",
            "(args.workspace/'output/alias.json').hardlink_to(args.workspace/'output/summary.json')"), {"MODEL_FAIL"}),
        "oversized-output": (good.replace("raise SystemExit(result.returncode)",
            "(args.workspace/'output/oversized.bin').write_bytes(b'x'*(9*1024*1024))"), {"MODEL_FAIL"}),
        "missing-reward": (good, {"UNSCORABLE"}),
        "malformed-reward": (good, {"UNSCORABLE"}),
        "verifier-timeout": (good, {"INFRA_ERROR"}),
    }

    def run(item):
        name, (installer, expected) = item
        task = root / "tasks" / name
        shutil.copytree(baseline, task)
        (task / "solution/solve.py").write_text(installer, encoding="utf-8", newline="\n")
        test = task / "tests/test.sh"
        if name == "protected-worker-double-fork":
            text = test.read_text().replace('if [ "$status" -eq 0 ]; then',
                'sleep 0.6\nif [ -e /tmp/hf-detached-survived ]; then exit 2; fi\n'
                'cp /tmp/hf-worker-proof.json /logs/verifier/worker-proof.json\n'
                'if [ "$status" -eq 0 ]; then')
            test.write_text(text, encoding="utf-8", newline="\n")
        elif name == "missing-reward":
            test.write_text("#!/bin/bash\nexit 2\n", encoding="utf-8", newline="\n")
        elif name == "malformed-reward":
            test.write_text("#!/bin/bash\nprintf 'nan\\n' > /logs/verifier/reward.txt\n", encoding="utf-8", newline="\n")
        elif name == "verifier-timeout":
            test.write_text("#!/bin/bash\nsleep 10\n", encoding="utf-8", newline="\n")
        harness = {"agent": "oracle", "trial_time_limit_sec": 180}
        if name == "verifier-timeout":
            harness["verifier_time_limit_sec"] = 1
        result = HarborAdapter().run(task, {}, harness, root / "runs" / name)
        evidence = {"probe": name, "expected": sorted(expected), "outcome": result["outcome"],
                    "reward": result["reward"], "error_code": result["error_code"],
                    "passed": result["outcome"] in expected,
                    "solver_sha256": hashlib.sha256((task / "solution/solve.py").read_bytes()).hexdigest(),
                    "test_sha256": hashlib.sha256(test.read_bytes()).hexdigest(),
                    "wall_time_s": result["wall_time_s"]}
        if name == "protected-worker-double-fork" and result["outcome"] == "PASS":
            files = list((root / "runs" / name).rglob("worker-proof.json"))
            evidence["worker"] = json.loads(files[0].read_text()) if len(files) == 1 else None
            evidence["passed"] = evidence["passed"] and evidence["worker"] is not None
        print(json.dumps(evidence), flush=True)
        return evidence

    with ThreadPoolExecutor(max_workers=2) as pool:
        selected = [(name, value) for name, value in scripts.items() if not args.probe or name in args.probe]
        if args.probe and (set(args.probe) - set(scripts)):
            parser.error("Unknown probe name")
        rows = list(pool.map(run, selected))
    summary = {"schema_version": 1, "scope": "Actual authored adversarial container controls; zero model calls",
               "model_calls": 0, "passed": all(row["passed"] for row in rows), "probes": rows}
    (root / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8", newline="\n")
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
