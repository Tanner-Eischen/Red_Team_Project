"""Content provenance without self-referential manifest checksums."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


def task_digest(task_path: Path | str) -> str:
    """Hash paths and bytes; only the manifest's own digest is omitted.

    Evidence belongs outside authored tasks. Any other addition, including a
    validation report written inside a task, therefore invalidates its digest.
    """
    root = Path(task_path)
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        if path.is_symlink():
            raise ValueError(f"Task packages cannot contain symlinks: {path}")
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        data = path.read_bytes()
        if relative == "manifest.json":
            manifest = json.loads(data)
            manifest.pop("artifact_sha256", None)
            data = json.dumps(manifest, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        name = relative.encode("utf-8")
        digest.update(len(name).to_bytes(8, "big"))
        digest.update(name)
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return digest.hexdigest()


def read_manifest(task_path: Path | str, *, verify: bool = True) -> dict:
    root = Path(task_path)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    if verify and manifest.get("artifact_sha256") != task_digest(root):
        raise ValueError("Task content differs from its immutable manifest")
    return manifest
