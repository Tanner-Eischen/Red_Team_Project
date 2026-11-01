Repair /app/pipeline.py. It must accept --input CSV --output DIRECTORY,
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
