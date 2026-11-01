"""Check committed example hashes without executing their programs."""

from pathlib import Path

from harborforge.authoring.provenance import read_manifest


def main():
    paths = sorted(Path("tasks/public_examples").glob("*/manifest.json"))
    if len(paths) != 12:
        raise ValueError("Expected the 12-task public pilot")
    for path in paths:
        manifest = read_manifest(path.parent)
        if manifest["cohort"] != "discovery":
            raise ValueError("Public examples cannot be represented as private holdout")
    print(f"Verified {len(paths)} immutable public task packages")


if __name__ == "__main__":
    main()
