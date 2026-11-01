"""Inspect every saved agent-image layer without extracting files onto the host."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile

from harborforge.authoring.provenance import read_manifest


def audit(task: Path, output: Path) -> dict:
    manifest = read_manifest(task)
    tag = "redforge-gate-" + manifest["artifact_sha256"][:16]
    image = json.loads(subprocess.check_output(["docker", "image", "inspect", tag]))[0]
    archive = output / (manifest["family"] + ".tar")
    subprocess.run(["docker", "image", "save", "--output", str(archive), tag], check=True, capture_output=True)
    private_hashes = {hashlib.sha256(path.read_bytes()).hexdigest() for path in (task / "tests").rglob("*")
                      if path.is_file() and path.name == "cases.json" or
                      path.is_file() and "fixtures" in path.parts and "0" not in path.parts and path.stat().st_size >= 64}
    # A fixed analytic input may legitimately be reused by public and hidden
    # cases. Its public bytes are not protected material; expected-case records
    # and private-only fixtures still must be absent from every image layer.
    public_hashes = {hashlib.sha256(path.read_bytes()).hexdigest()
                     for path in (task / "environment/public").rglob("*") if path.is_file()}
    public_duplicates = len(private_hashes & public_hashes)
    private_hashes -= public_hashes
    matches = []
    layers = []
    with tarfile.open(archive) as saved:
        index = json.load(saved.extractfile("manifest.json"))[0]
        for layer in index["Layers"]:
            entries = 0
            with tarfile.open(fileobj=saved.extractfile(layer), mode="r|*") as filesystem:
                for member in filesystem:
                    entries += 1
                    name = member.name.lstrip("./")
                    if name.split("/", 1)[0] in {"tests", "solution", "controls", "private"}:
                        matches.append({"layer": layer, "path": name, "reason": "private root path"})
                    if member.isfile() and member.size <= 8 * 1024 * 1024:
                        content_hash = hashlib.sha256(filesystem.extractfile(member).read()).hexdigest()
                        if content_hash in private_hashes:
                            matches.append({"layer": layer, "path": name, "reason": "private expected/hidden fixture bytes"})
            layers.append({"layer": layer, "entries": entries})
    proof = {"family": manifest["family"], "task_sha256": manifest["artifact_sha256"],
             "image_id": image["Id"], "image_rootfs_layers": image["RootFS"]["Layers"],
             "layers_inspected": layers, "private_content_matches": matches,
             "private_large_content_hashes_checked": len(private_hashes),
             "already_public_duplicate_fixture_hashes_excluded": public_duplicates,
             "image_user": image["Config"]["User"], "image_environment_names":
                 [item.split("=", 1)[0] for item in image["Config"].get("Env", [])],
             "passed": not matches and image["Config"]["User"] == "agent",
             "scope": "Standalone quality-gate image built from the same immutable public agent context; actual Harbor role IDs are recorded separately. No image files extracted onto host."}
    return proof


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=False)
    rows = [audit(path.parent, args.output_dir) for path in sorted(args.tasks.glob("*/manifest.json"))]
    result = {"schema_version": 1, "passed": bool(rows) and all(row["passed"] for row in rows), "images": rows}
    (args.output_dir / "image-audit.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"passed": result["passed"], "images": len(rows)}))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
