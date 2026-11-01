# HF-01 native container acceptance

Actual Harbor 0.13.2 and Linux Docker 29.1.3 controls passed for three fresh family-v2 seed-123 tasks (18 controls), plus a real immutable nullable-revision child (7 controls). Eight hostile fixtures and a targeted final private-PID-namespace repeat passed. All three saved public agent-image layer audits passed. The locked native Linux suite passed 218 tests and Ruff. These are author/control outcomes with zero model calls, not measured model performance.

`summary.json` binds the compact evidence. `family-controls.json` records independent rewards and actual agent/verifier runtime settings. `boundary-probes.json` covers private reads, forged rewards, low-UID limits/capabilities, network denial, detached double-fork cleanup, symlinks/hardlinks/oversized transfer, missing/malformed reward and verifier timeout. `image-audit.json` inspects all saved image layers without extracting them onto the host. `source-manifest.json` distinguishes raw executed source bytes from canonical Git LF bytes. Raw execution data and saved images remain in the private native cache; no large images or hidden holdout corpus is committed here.

Reproduce with the locked Linux environment:

```sh
python scripts/run_container_controls.py --seed 123 --output-dir artifacts/hf01-controls-NEW
python scripts/probe_container_boundary.py --output-dir artifacts/hf01-boundary-NEW
python scripts/audit_container_images.py --tasks artifacts/hf01-controls-NEW/tasks --output-dir artifacts/hf01-images-NEW
```

New destination directories are required; existing evidence is never overwritten. Actual execution found and corrected a missing separate-verifier Dockerfile and missing GeoTIFF OS library, both invisible to local controls. The supported custom environment removes host mounts, uses read-only root and bounded writable tmpfs, enforces network/CPU/memory/PID/file limits and streams only bounded regular files. The judge runs separately and invokes candidate code as UID 65534 with additional limits. UID-wide cleanup requires a private root-owned marker created only after inspecting a private container PID namespace; local author controls never use a generic Docker marker to kill host UIDs.

A reviewed task contract and explicitly approved pilot budget remain prerequisites for paid model trials. No control result here proves an empirical task-hardening effect or model ranking.
