# Task authoring and controls

The implemented families are `scientific-etl`, `geospatial-raster`, and
`repository-debug`. Their data is synthetic. These fixtures must not be described
as real sensor, satellite, or production application data.

`redforge task generate --family scientific-etl --seed 123 --output tasks/generated`
creates a new immutable Harbor package. Generation refuses to overwrite an
existing version. Aliases from the design (`python-data-validation` and
`software-debugging`) resolve to the corresponding implemented family.

Each package contains public instructions, an `environment/` build context,
private `solution/` and `tests/`, alternate and deliberately broken controls, and
a checksum-bearing `manifest.json`. Evidence must be stored outside the task;
adding a validation report inside the package changes its checksum. All package
files and manifest metadata are covered, with only the self checksum omitted.

The agent repairs `/app/pipeline.py`. Its CLI accepts `--input` and `--output`.
The verifier checks the submitted source on independent hidden fixtures,
including repeat execution. It never runs the oracle to produce an agent score.
Only the submitted source and output directory are copied to Harbor's separate
verifier environment; the original agent environment cannot overwrite trusted
verifier fixtures. The verifier checks submitted public output before rerunning
the source against hidden inputs, so missing artifacts cannot pass on source
correctness alone.

On Linux, the verifier protects `/tests` and `/logs/verifier` with root-only
permissions, then executes submitted programs with uid/gid 65534, a minimal
environment, per-case timeouts, CPU and output-file limits. Candidate-program
failure is a scored incorrect answer; a crash in the verifier itself leaves the
reward absent. Local Windows controls verify behavior only; they do not certify
container isolation or authorize model evaluation.

Each family has an oracle, an independently written alternate solution, the
initial incorrect implementation, and a shortcut/resampling negative control.
No-op retains the broken public source. The ETL verifier uses independently
computed Decimal fixture expectations; raster expectations use analytic Web
Mercator pixel-center sampling rather than the oracle's reproject function;
interval expectations enumerate covered integer cells rather than duplicate the
oracle's interval algorithm.

`redforge task revise` creates a discovery-only child with unchanged public
semantics and more interacting cases. Default operators are
`constraint-interaction`, `grid-expansion`, and `overlap-density`, respectively.
Lineage and the parent checksum are preserved; calibration and holdout tasks are
rejected. A child still needs fresh controls and matched agent trials. A new
version alone is not evidence that difficulty increased.

The old COBOL generator is retained as legacy code. Unimplemented legacy
weakness categories fail closed rather than generating echo tasks. Legacy
results do not establish scientific benchmark validity.
