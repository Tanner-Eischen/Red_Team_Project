"""Independent protected verifier copied into authored task packages.

This module is a template asset, not imported by generation or scoring.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def _drop_privileges():
    os.setgroups([])
    os.setgid(65534)
    os.setuid(65534)
    import resource
    resource.setrlimit(resource.RLIMIT_FSIZE, (8 * 1024 * 1024, 8 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_CPU, (15, 15))


def _run(command, sandbox):
    environment = {"PATH": os.defpath, "HOME": str(sandbox), "PYTHONDONTWRITEBYTECODE": "1"}
    if os.name == "nt" and "SYSTEMROOT" in os.environ:
        environment["SYSTEMROOT"] = os.environ["SYSTEMROOT"]
    options = {}
    if os.name == "posix" and os.geteuid() == 0:
        options["preexec_fn"] = _drop_privileges
    try:
        result = subprocess.run(command, cwd=sandbox, env=environment, stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20, **options)
    except subprocess.TimeoutExpired:
        raise AssertionError("Submitted program exceeded per-case time limit") from None
    assert result.returncode == 0, "Submitted program failed during behavioral test"


def _read_json(path):
    assert path.is_file() and not path.is_symlink(), "Missing or symlinked output"
    assert path.stat().st_size <= 1024 * 1024, "Output too large"
    try:
        return json.loads(path.read_text(encoding="utf-8"),
                          parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    except (ValueError, UnicodeError):
        raise AssertionError("Invalid JSON output") from None


def _equal(actual, expected, tolerance=1e-6):
    if isinstance(expected, dict):
        assert isinstance(actual, dict) and set(actual) == set(expected), "Wrong object schema"
        for key in expected:
            _equal(actual[key], expected[key], tolerance)
    elif isinstance(expected, list):
        assert isinstance(actual, list) and len(actual) == len(expected), "Wrong array schema"
        for left, right in zip(actual, expected):
            _equal(left, right, tolerance)
    elif isinstance(expected, bool) or expected is None or isinstance(expected, str):
        assert type(actual) is type(expected) and actual == expected, "Value mismatch"
    elif isinstance(expected, int):
        assert type(actual) is int and actual == expected, "Integer value mismatch"
    else:
        assert type(actual) in (int, float) and math.isfinite(actual), "Non-finite numeric value"
        assert math.isclose(actual, expected, rel_tol=0, abs_tol=tolerance), "Numeric value mismatch"


def _check_raster(output, expected):
    import numpy as np
    import rasterio
    path = output / "mosaic.tif"
    assert path.is_file() and not path.is_symlink(), "Missing raster"
    assert path.stat().st_size < 1024 * 1024, "Raster too large"
    try:
        with rasterio.open(path) as raster:
            assert raster.crs == rasterio.crs.CRS.from_string(expected["crs"]), "CRS mismatch"
            assert list(raster.transform) == expected["transform"], "Affine mismatch"
            assert raster.count == 1 and raster.dtypes == ("float32",), "Band/dtype mismatch"
            assert raster.nodata == -9999, "NoData metadata mismatch"
            assert raster.driver == "GTiff", "Output is not a GeoTIFF"
            assert (raster.height, raster.width) == (len(expected["pixels"]), len(expected["pixels"][0])), "Grid shape mismatch"
            actual = raster.read(1)
        values = np.asarray(expected["pixels"], dtype="float32")
        assert actual.shape == values.shape, "Grid shape mismatch"
        assert np.array_equal(actual == -9999, values == -9999), "NoData mask mismatch"
        assert np.allclose(actual, values, atol=1e-5, rtol=0), "Pixel mismatch"
    except rasterio.errors.RasterioIOError:
        raise AssertionError("Invalid GeoTIFF output") from None
    _equal(_read_json(output / "summary.json"), expected["summary"], 1e-5)


def verify(workspace):
    tests = Path(__file__).resolve().parent
    cases = json.loads((tests / "cases.json").read_text(encoding="utf-8"))
    candidate = workspace / "pipeline.py"
    assert candidate.is_file() and not candidate.is_symlink(), "Missing submitted pipeline.py"
    assert candidate.stat().st_size < 1024 * 1024, "Submitted source too large"
    public_output = workspace / "output"
    assert public_output.is_dir() and not public_output.is_symlink(), "Missing submitted output directory"
    public_case = cases[0]
    if public_case["kind"] == "raster":
        _check_raster(public_output, public_case["expected"])
    else:
        _equal(_read_json(public_output / public_case["output"]), public_case["expected"])
    for index, case in enumerate(cases):
        with tempfile.TemporaryDirectory(prefix="redforge-check-") as temporary:
            sandbox = Path(temporary)
            sandbox.chmod(0o755)
            source = sandbox / "pipeline.py"
            shutil.copyfile(candidate, source)
            source.chmod(0o644)
            fixture = sandbox / "input"
            shutil.copytree(tests / "fixtures" / str(index), fixture)
            for path in fixture.rglob("*"):
                path.chmod(0o755 if path.is_dir() else 0o644)
            fixture.chmod(0o755)
            output = sandbox / "output"
            output.mkdir(mode=0o777)
            output.chmod(0o777)
            input_path = fixture if case["kind"] == "raster" else fixture / case["filename"]
            # Repeat the exact same call to check overwrite/idempotence behavior.
            for _ in range(2):
                _run([sys.executable, "-I", str(source), "--input", str(input_path), "--output", str(output)], sandbox)
                if case["kind"] == "raster":
                    _check_raster(output, case["expected"])
                else:
                    _equal(_read_json(output / case["output"]), case["expected"])
            if case["kind"] == "repository":
                check = ("import importlib.util,json; "
                         f"s=importlib.util.spec_from_file_location('submitted',{str(source)!r}); "
                         "m=importlib.util.module_from_spec(s); s.loader.exec_module(m); "
                         f"x=json.loads({json.dumps(case['input'])!r}); "
                         "result=m.merge_intervals(x); "
                         "from pathlib import Path; "
                         f"Path({str(output / 'function.json')!r}).write_text("
                         "json.dumps({'result':result,'input_after':x}),encoding='utf-8')")
                _run([sys.executable, "-I", "-c", check], sandbox)
                # Expected values stay exclusively in this protected process;
                # never place them in child argv, source, or environment.
                observed = _read_json(output / "function.json")
                _equal(observed, {"result": case["expected"], "input_after": case["input"]})
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()
    try:
        raise SystemExit(verify(args.workspace))
    except AssertionError as error:
        sys.stderr.write(str(error) + "\n")
        raise SystemExit(1)
