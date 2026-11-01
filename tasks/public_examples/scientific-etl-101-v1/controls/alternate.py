import argparse
from pathlib import Path
import subprocess
import sys

parser = argparse.ArgumentParser()
parser.add_argument('--workspace', type=Path, required=True)
args = parser.parse_args()
args.workspace.mkdir(parents=True, exist_ok=True)
target = args.workspace / 'pipeline.py'
target.write_text("import argparse\nimport csv\nimport json\nfrom decimal import Decimal, ROUND_HALF_EVEN\nfrom pathlib import Path\n\n\ndef transform(path):\n    rows = list(csv.DictReader(open(path, newline='', encoding='utf-8')))\n    keys = sorted({(row['station_id'], row['timestamp']) for row in rows})\n    groups = {}\n    for station, timestamp in keys:\n        row = max((r for r in rows if (r['station_id'], r['timestamp']) == (station, timestamp)),\n                  key=lambda r: int(r['revision']))\n        if row['status'] != 'ok':\n            continue\n        try:\n            value = Decimal(row['value'])\n        except Exception:\n            continue\n        if not value.is_finite():\n            continue\n        if row['unit'] == 'K':\n            value -= Decimal('273.15')\n        if row['unit'] == 'F':\n            value = (value - 32) * 5 / 9\n        groups.setdefault(station, []).append(value)\n    return [dict(station_id=k, count=len(v), mean_c=float((sum(v) / len(v)).quantize(Decimal('.000001'),\n                                                                                   rounding=ROUND_HALF_EVEN)))\n            for k, v in sorted(groups.items())]\n\n\nif __name__ == '__main__':\n    parser = argparse.ArgumentParser()\n    parser.add_argument('--input', required=True)\n    parser.add_argument('--output', required=True)\n    args = parser.parse_args()\n    target = Path(args.output)\n    target.mkdir(parents=True, exist_ok=True)\n    (target / 'summary.json').write_text(json.dumps(transform(args.input), allow_nan=False), encoding='utf-8')\n", encoding='utf-8')
result = subprocess.run([sys.executable, str(target), '--input',
                         str(args.workspace / 'input/observations.csv'), '--output',
                         str(args.workspace / 'output')], check=False)
raise SystemExit(result.returncode)
