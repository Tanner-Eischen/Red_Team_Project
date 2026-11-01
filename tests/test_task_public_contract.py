"""Published constraints mirror the actual grading/resource contract."""

import pytest

from harborforge.authoring.provenance import read_manifest
from harborforge.task_families.registry import FAMILIES, generate_task


@pytest.mark.parametrize("family", FAMILIES)
def test_every_new_task_discloses_enforced_runtime_and_artifact_limits(tmp_path, family):
    if family == "geospatial-raster":
        pytest.importorskip("rasterio")
    task = generate_task(family, 20261101, tmp_path)
    instruction = (task / "instruction.md").read_bytes()
    assert instruction == (task / "environment/public/README.md").read_bytes()
    text = instruction.decode()
    for wording in ("1 CPU", "2 GiB", "64-process", "/app 32 MiB", "/tmp 64 MiB",
                    "128 paths", "8 MiB per file", "16 MiB total", "strictly smaller than 1 MiB",
                    "1 MiB (inclusive)", "regular, singly linked", "20-second wall", "15-second CPU",
                    "1 GiB address-space", "16-process limit", "64-file-descriptor", "Network access is disabled"):
        assert wording in text
    manifest = read_manifest(task)
    contract = manifest["artifact_contract"]
    assert contract["source_max_bytes_exclusive"] == contract["raster_max_bytes_exclusive"] == 1048576
    assert contract["json_max_bytes_inclusive"] == 1048576
    assert "source_max_bytes" not in contract
    assert contract["transfer_max_paths_inclusive"] == 128
    assert contract["container"]["memory_bytes"] == 2 * 1024 ** 3
    assert contract["protected_execution"]["address_space_bytes"] == 1024 ** 3
    assert len(manifest["requirements"]) == 4
    assert manifest["requirements"][-1]["id"].endswith("-R04")
    assert "resource limits" in manifest["requirements"][-1]["verifier_assertion"]
    assert manifest["private_validation_hash"] not in text
    assert "source, input and the current working directory\nare read-only" in text
    assert "place scratch files in /tmp or the supplied --output\ndirectory" in text
