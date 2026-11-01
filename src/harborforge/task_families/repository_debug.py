"""Real interval-merging repository repair with exhaustive integer-cell checks."""
from __future__ import annotations

import random


INSTRUCTION = """Repair /app/pipeline.py, a broken interval normalization utility.
Accept --input JSON --output DIRECTORY. JSON input is an array of [start,end]
integer pairs, each describing a half-open interval [start,end). Discard empty
intervals (start == end). If ANY pair is reversed (start > end), emit exactly
{\"error\":\"reversed_interval\"}; do not silently swap its endpoints.
Otherwise emit {\"intervals\":[[start,end],...],\"covered\":N} in output/result.json,
where intervals are the sorted disjoint union; touching intervals MUST merge,
duplicates collapse, negative coordinates are allowed, and covered is total
integer length (sum of end-start). Empty input yields intervals=[] and covered=0.
The importable function merge_intervals(intervals) must return that same object
without modifying the input. CLI must overwrite outputs idempotently.
Use Python standard library. Input fixture: /app/input/intervals.json. Run the
program and leave repaired /app/pipeline.py. Hidden tests use other valid JSONs
under this contract. All fixture data is synthetic.
"""

SOURCE = r'''import argparse
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
'''

ALTERNATE = r'''import argparse
import json
from pathlib import Path


def merge_intervals(intervals):
    if any(a > b for a, b in intervals):
        return {'error': 'reversed_interval'}
    events = {}
    for start, end in intervals:
        if start < end:
            events[start] = events.get(start, 0) + 1
            events[end] = events.get(end, 0) - 1
    count = 0
    begin = None
    result = []
    for position, change in sorted(events.items()):
        previous = count
        count += change
        if previous == 0 and count > 0:
            begin = position
        if previous > 0 and count == 0:
            result.append([begin, position])
    return {'intervals': result, 'covered': sum(b-a for a, b in result)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    result = merge_intervals(json.loads(Path(args.input).read_text(encoding='utf-8')))
    target = Path(args.output)
    target.mkdir(parents=True, exist_ok=True)
    (target / 'result.json').write_text(json.dumps(result), encoding='utf-8')
'''

BUGGY = SOURCE.replace("start <= merged[-1][1]", "start < merged[-1][1]")


def fixture(seed: int, level: int = 0, *, variant: str | None = None) -> tuple[list, dict]:
    if variant not in {None, "repository-boundary-overlap", "repository-input-ordering"}:
        raise ValueError("Unsupported repository fixture variant")
    rng = random.Random(seed)
    intervals = [[-4, 0], [0, 3], [7, 7], [9, 11], [10, 14], [9, 11]]
    intervals += [[value, value + rng.randint(0, 5)] for value in rng.sample(range(-20, 25), 4 + level)]
    if variant == "repository-boundary-overlap":
        start = 100 + level * 10
        intervals += [[start, start + 2], [start + 2, start + 5], [start + 5, start + 5], [start, start + 2]]
    elif variant == "repository-input-ordering":
        intervals += [[100 + n * 10, 103 + n * 10] for n in range(3 + level)]
    rng.shuffle(intervals)
    if variant == "repository-input-ordering":
        intervals.sort(reverse=True)
    # Independent verifier fixture expectation: finite integer cells, rather
    # than duplicating either interval-merging implementation.
    occupied = {point for start, end in intervals for point in range(start, end)}
    expected = []
    for point in sorted(occupied):
        if expected and point == expected[-1][1]:
            expected[-1][1] += 1
        else:
            expected.append([point, point + 1])
    return intervals, {"intervals": expected, "covered": len(occupied)}
