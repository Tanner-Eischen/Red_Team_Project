import argparse
from pathlib import Path
import subprocess
import sys

parser = argparse.ArgumentParser()
parser.add_argument('--workspace', type=Path, required=True)
args = parser.parse_args()
args.workspace.mkdir(parents=True, exist_ok=True)
target = args.workspace / 'pipeline.py'
target.write_text("import argparse\nimport json\nfrom pathlib import Path\n\n\ndef merge_intervals(intervals):\n    if any(start > end for start, end in intervals):\n        return {'error': 'reversed_interval'}\n    merged = []\n    for start, end in sorted((a, b) for a, b in intervals if a < b):\n        if merged and start < merged[-1][1]:\n            merged[-1][1] = max(merged[-1][1], end)\n        else:\n            merged.append([start, end])\n    return {'intervals': merged, 'covered': sum(b - a for a, b in merged)}\n\n\nif __name__ == '__main__':\n    parser = argparse.ArgumentParser()\n    parser.add_argument('--input', required=True)\n    parser.add_argument('--output', required=True)\n    args = parser.parse_args()\n    result = merge_intervals(json.loads(Path(args.input).read_text(encoding='utf-8')))\n    target = Path(args.output)\n    target.mkdir(parents=True, exist_ok=True)\n    (target / 'result.json').write_text(json.dumps(result), encoding='utf-8')\n", encoding='utf-8')
result = subprocess.run([sys.executable, str(target), '--input',
                         str(args.workspace / 'input/intervals.json'), '--output',
                         str(args.workspace / 'output')], check=False)
raise SystemExit(result.returncode)
