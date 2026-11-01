# Public scientific software pilot

Twelve synthetic discovery tasks: seeds 100–103 for each of `scientific-etl`, `geospatial-raster` and `repository-debug`.

Each package contains public instructions/environment, protected oracle/verifier/controls and an immutable content manifest. Only the `environment/` subdirectory is the agent build context. Publishing the host package exposes authoring answers publicly, so these examples cannot be claimed as a private holdout for future measurement.

See [local validation evidence](../../reports/public-pilot/local-controls.json) and [the static report](../../reports/public-pilot/index.html). These files contain actual author-control results and zero model trials. Actual container isolation and model performance remain unverified.

Generate/verify with `python scripts/build_public_pilot.py --validate`; new tasks use `redforge task generate`. Never edit a previously evaluated package in place; create a new version and revalidate it.
