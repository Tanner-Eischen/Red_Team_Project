import argparse
import csv
import json
import math
from pathlib import Path


def transform(path):
    latest = {}
    with open(path, newline='', encoding='utf-8') as stream:
        for row in csv.DictReader(stream):
            key = (row['station_id'], row['timestamp'])
            if key not in latest or int(row['revision']) > int(latest[key]['revision']):
                latest[key] = row
    grouped = {}
    for row in latest.values():
        if row['status'] not in ('ok', ''):
            continue
        try:
            value = float(row['value'])
        except ValueError:
            continue
        if not math.isfinite(value):
            continue
        if row['unit'] == 'F':
            value = (value - 32) * 5 / 9
        elif row['unit'] == 'K':
            value -= 273.15
        grouped.setdefault(row['station_id'], []).append(value)
    return [dict(station_id=key, count=len(values),
                 mean_c=round(math.fsum(sorted(values)) / len(values), 6))
            for key, values in sorted(grouped.items())]


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    target = Path(args.output)
    target.mkdir(parents=True, exist_ok=True)
    (target / 'summary.json').write_text(json.dumps(transform(args.input), allow_nan=False), encoding='utf-8')
