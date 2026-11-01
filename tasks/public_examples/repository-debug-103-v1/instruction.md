Repair /app/pipeline.py, a broken interval normalization utility.
Accept --input JSON --output DIRECTORY. JSON input is an array of [start,end]
integer pairs, each describing a half-open interval [start,end). Discard empty
intervals (start == end). If ANY pair is reversed (start > end), emit exactly
{"error":"reversed_interval"}; do not silently swap its endpoints.
Otherwise emit {"intervals":[[start,end],...],"covered":N} in output/result.json,
where intervals are the sorted disjoint union; touching intervals MUST merge,
duplicates collapse, negative coordinates are allowed, and covered is total
integer length (sum of end-start). Empty input yields intervals=[] and covered=0.
The importable function merge_intervals(intervals) must return that same object
without modifying the input. CLI must overwrite outputs idempotently.
Use Python standard library. Input fixture: /app/input/intervals.json. Run the
program and leave repaired /app/pipeline.py. Hidden tests use other valid JSONs
under this contract. All fixture data is synthetic.
