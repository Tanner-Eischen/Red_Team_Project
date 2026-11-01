"""Small, verifiable filesystem object store; object names never contain user paths."""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from pathlib import Path


class ArtifactStore:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, digest: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("Artifact digest must be a lowercase SHA-256")
        return self.root / digest[:2] / digest[2:]

    def put(self, content: bytes) -> dict:
        digest = hashlib.sha256(content).hexdigest()
        path = self.path_for(digest)
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".pending-", delete=False) as stream:
            pending = Path(stream.name)
            try:
                stream.write(content)
            except BaseException:
                pending.unlink(missing_ok=True)
                raise
        try:
            try:
                # Linking a fully written object makes publication atomic and never replaces prior content.
                os.link(pending, path)
            except FileExistsError:
                if self.get(digest) != content:
                    raise ValueError("Existing content-addressed artifact is corrupt") from None
        finally:
            pending.unlink(missing_ok=True)
        return {"sha256": digest, "relative_path": path.relative_to(self.root).as_posix(),
                "byte_length": len(content)}

    def put_file(self, source: Path | str) -> dict:
        path = Path(source)
        if path.is_symlink() or not path.is_file():
            raise ValueError("Artifact source must be an ordinary file")
        return self.put(path.read_bytes())

    def get(self, digest: str) -> bytes:
        data = self.path_for(digest).read_bytes()
        if hashlib.sha256(data).hexdigest() != digest:
            raise ValueError("Artifact hash mismatch")
        return data
