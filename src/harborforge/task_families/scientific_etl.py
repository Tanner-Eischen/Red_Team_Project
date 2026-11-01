"""CSV repair task with independent, fixture-derived expectations."""
from __future__ import annotations

import csv
import io
import random
from collections import defaultdict
from decimal import Decimal


INSTRUCTION = """Repair /app/pipeline.py. It must accept --input CSV --output DIRECTORY,
read the CSV, and emit summary.json as a JSON array sorted by station_id. Each row
has exactly station_id (string), count (integer), mean_c (number). The input has
station_id,timestamp,value,unit,status,revision; irrelevant columns may occur.
Timestamps are UTC ISO-8601 strings ending Z. For duplicate (station_id,timestamp),
select the row with largest integer revision BEFORE checking status/value. If that
selected row is invalid, discard that timestamp; do not resurrect an older value.
Revisions per duplicate key are unique. A row is valid only when status is exactly
'ok' and value is a finite number. Blank status is unknown and must be discarded.
Units are C (Celsius), F (convert (value-32)*5/9), and K (value-273.15).
Aggregate valid readings by station after conversion; omit stations with none.
Round mean_c to six decimals using round-to-even; numeric tolerance is 1e-6.
Overwrite output deterministically on repeated calls; input order must not matter.
Use Python standard library only. Input is /app/input/observations.csv. Run the
program on it and leave repaired source /app/pipeline.py. Hidden tests use other
CSVs under exactly this contract. Synthetic fixtures, no real sensor observations.
"""

SOURCE = r'''import argparse
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
        if row['status'] != 'ok':
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
'''

ALTERNATE = r'''import argparse
import csv
import json
from decimal import Decimal, ROUND_HALF_EVEN
from pathlib import Path


def transform(path):
    rows = list(csv.DictReader(open(path, newline='', encoding='utf-8')))
    keys = sorted({(row['station_id'], row['timestamp']) for row in rows})
    groups = {}
    for station, timestamp in keys:
        row = max((r for r in rows if (r['station_id'], r['timestamp']) == (station, timestamp)),
                  key=lambda r: int(r['revision']))
        if row['status'] != 'ok':
            continue
        try:
            value = Decimal(row['value'])
        except Exception:
            continue
        if not value.is_finite():
            continue
        if row['unit'] == 'K':
            value -= Decimal('273.15')
        if row['unit'] == 'F':
            value = (value - 32) * 5 / 9
        groups.setdefault(station, []).append(value)
    return [dict(station_id=k, count=len(v), mean_c=float((sum(v) / len(v)).quantize(Decimal('.000001'),
                                                                                   rounding=ROUND_HALF_EVEN)))
            for k, v in sorted(groups.items())]


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    target = Path(args.output)
    target.mkdir(parents=True, exist_ok=True)
    (target / 'summary.json').write_text(json.dumps(transform(args.input), allow_nan=False), encoding='utf-8')
'''

BUGGY = SOURCE.replace("if row['status'] != 'ok':", "if row['status'] not in ('ok', ''):")


def fixture(seed: int, level: int = 0, *, variant: str | None = None) -> tuple[str, list[dict]]:
    if variant not in {None, "etl-nullable-revisions", "etl-mixed-unit-ordering"}:
        raise ValueError("Unsupported ETL fixture variant")
    rng = random.Random(seed)
    records = []
    for station in ("alpha", "beta", "empty"):
        for day in range(1, 7 + level):
            celsius = Decimal(rng.randint(-150, 400)) / 10
            unit = rng.choice(("C", "F", "K"))
            raw = celsius if unit == "C" else celsius * 9 / 5 + 32 if unit == "F" else celsius + Decimal("273.15")
            records.append(dict(station_id=station, timestamp=f"2026-01-{day:02d}T00:00:00Z", value=str(raw),
                                unit=unit, status="" if station == "empty" or day == 2 else "ok", revision="0"))
        records.append(dict(station_id=station, timestamp="2026-01-01T00:00:00Z", value="999", unit="C",
                            status="bad", revision="2"))
        records.append(dict(station_id=station, timestamp="2026-01-03T00:00:00Z", value="NaN", unit="C",
                            status="ok", revision="3"))
        records.append(dict(station_id=station, timestamp="2026-01-04T00:00:00Z", value="Infinity", unit="C",
                            status="ok", revision="4"))
    if variant == "etl-nullable-revisions":
        for station in ("alpha", "beta"):
            for day in range(20, 20 + max(1, level)):
                key = dict(station_id=station, timestamp=f"2026-02-{day - 19:02d}T00:00:00Z", unit="C")
                records += [{**key, "value": "12.5", "status": "ok", "revision": "0"},
                            {**key, "value": "25", "status": "", "revision": str(10 + level)}]
    elif variant == "etl-mixed-unit-ordering":
        for index, (unit, value) in enumerate((("C", "10"), ("F", "50"), ("K", "283.15"))):
            records.append(dict(station_id="unit-control", timestamp=f"2026-03-{index + 1:02d}T00:00:00Z",
                                value=value, unit=unit, status="ok", revision=str(level)))
    rng.shuffle(records)
    if seed % 5 == 0:
        for row in records:
            row["status"] = ""
    # Independent expected-value derivation: enumerate each key, select max
    # revision, then Decimal arithmetic. No oracle implementation is imported.
    groups = defaultdict(list)
    for key in sorted({(row["station_id"], row["timestamp"]) for row in records}):
        selected = sorted((row for row in records if (row["station_id"], row["timestamp"]) == key),
                          key=lambda row: -int(row["revision"]))[0]
        number = Decimal(selected["value"])
        if selected["status"] != "ok" or not number.is_finite():
            continue
        if selected["unit"] == "F":
            number = (number - 32) / Decimal("1.8")
        elif selected["unit"] == "K":
            number -= Decimal("273.15")
        groups[key[0]].append(number)
    expected = [dict(station_id=station, count=len(values), mean_c=float(round(sum(values) / len(values), 6)))
                for station, values in sorted(groups.items())]
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=list(records[0]))
    writer.writeheader()
    writer.writerows(records)
    return stream.getvalue(), expected
