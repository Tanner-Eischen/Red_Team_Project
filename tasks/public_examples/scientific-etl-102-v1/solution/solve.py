import argparse
from pathlib import Path
import subprocess
import sys

parser = argparse.ArgumentParser()
parser.add_argument('--workspace', type=Path, required=True)
args = parser.parse_args()
args.workspace.mkdir(parents=True, exist_ok=True)
target = args.workspace / 'pipeline.py'
target.write_text("import argparse\nimport csv\nimport json\nimport math\nfrom pathlib import Path\n\n\ndef transform(path):\n    latest = {}\n    with open(path, newline='', encoding='utf-8') as stream:\n        for row in csv.DictReader(stream):\n            key = (row['station_id'], row['timestamp'])\n            if key not in latest or int(row['revision']) > int(latest[key]['revision']):\n                latest[key] = row\n    grouped = {}\n    for row in latest.values():\n        if row['status'] != 'ok':\n            continue\n        try:\n            value = float(row['value'])\n        except ValueError:\n            continue\n        if not math.isfinite(value):\n            continue\n        if row['unit'] == 'F':\n            value = (value - 32) * 5 / 9\n        elif row['unit'] == 'K':\n            value -= 273.15\n        grouped.setdefault(row['station_id'], []).append(value)\n    return [dict(station_id=key, count=len(values),\n                 mean_c=round(math.fsum(sorted(values)) / len(values), 6))\n            for key, values in sorted(grouped.items())]\n\n\nif __name__ == '__main__':\n    parser = argparse.ArgumentParser()\n    parser.add_argument('--input', required=True)\n    parser.add_argument('--output', required=True)\n    args = parser.parse_args()\n    target = Path(args.output)\n    target.mkdir(parents=True, exist_ok=True)\n    (target / 'summary.json').write_text(json.dumps(transform(args.input), allow_nan=False), encoding='utf-8')\n", encoding='utf-8')
result = subprocess.run([sys.executable, str(target), '--input',
                         str(args.workspace / 'input/observations.csv'), '--output',
                         str(args.workspace / 'output')], check=False)
raise SystemExit(result.returncode)
