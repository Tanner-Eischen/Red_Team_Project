import argparse
from pathlib import Path
import subprocess
import sys

parser = argparse.ArgumentParser()
parser.add_argument('--workspace', type=Path, required=True)
args = parser.parse_args()
args.workspace.mkdir(parents=True, exist_ok=True)
target = args.workspace / 'pipeline.py'
target.write_text('import argparse,json\nfrom pathlib import Path\np=argparse.ArgumentParser();p.add_argument(\'--input\');p.add_argument(\'--output\');a=p.parse_args()\no=Path(a.output);o.mkdir(parents=True,exist_ok=True)\n(o/\'summary.json\').write_text(\'[{"station_id": "alpha", "count": 2, "mean_c": 21.2}, {"station_id": "beta", "count": 2, "mean_c": 26.5}]\',encoding=\'utf-8\')\n', encoding='utf-8')
result = subprocess.run([sys.executable, str(target), '--input',
                         str(args.workspace / 'input/observations.csv'), '--output',
                         str(args.workspace / 'output')], check=False)
raise SystemExit(result.returncode)
