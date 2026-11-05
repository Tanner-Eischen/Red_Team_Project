import argparse
import json
from pathlib import Path


def merge_intervals(intervals):
    if any(start > end for start, end in intervals):
        return {'error': 'reversed_interval'}
    merged = []
    for start, end in sorted((a, b) for a, b in intervals if a < b):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return {'intervals': merged, 'covered': sum(b - a for a, b in merged)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    result = merge_intervals(json.loads(Path(args.input).read_text(encoding='utf-8')))
    target = Path(args.output)
    target.mkdir(parents=True, exist_ok=True)
    (target / 'result.json').write_text(json.dumps(result), encoding='utf-8')
