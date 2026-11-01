"""Tiny real GeoTIFFs with analytic center-sampling verifier expectations."""
from __future__ import annotations

import math
from pathlib import Path


INSTRUCTION = """Repair /app/pipeline.py to mosaic two synthetic GeoTIFFs. Accept
--input DIRECTORY --output DIRECTORY. Input contains reference.tif (target grid,
EPSG:4326) and secondary.tif (different grid, EPSG:3857). Each has a single float32
band and NoData=-9999. Reproject secondary onto the EXACT reference CRS, affine,
width and height using nearest-neighbor sampling at target pixel centers. A
target center outside the source extent is missing. Never treat NoData as zero.
For each pixel, average the two valid values when both exist, retain a sole valid
value, and emit -9999 if neither exists. Save float32 output/mosaic.tif with exact
target georeferencing and NoData metadata. Also write output/summary.json with
exactly valid_count (integer) and mean (number or null for no valid cells).
Pixel and mean tolerance: 1e-5 absolute. Repeat execution must be idempotent.
Use NumPy/rasterio already installed. Public input directory: /app/input.
Run the program there and leave repaired /app/pipeline.py. Hidden tests use
different raster values/NoData under the same contract. These are generated
synthetic fixtures, not satellite observations or a real geospatial dataset.
"""

SOURCE = r'''import argparse
import json
from pathlib import Path
import numpy as np
import rasterio
from rasterio.warp import reproject, Resampling


def mosaic(source, destination):
    source = Path(source)
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    with rasterio.open(source / 'reference.tif') as reference, rasterio.open(source / 'secondary.tif') as other:
        a = reference.read(1)
        b = np.full(a.shape, -9999, dtype='float32')
        reproject(source=rasterio.band(other, 1), destination=b,
                  src_transform=other.transform, src_crs=other.crs,
                  dst_transform=reference.transform, dst_crs=reference.crs,
                  src_nodata=-9999, dst_nodata=-9999, resampling=Resampling.nearest)
        valid_a = a != -9999
        valid_b = b != -9999
        count = valid_a.astype('int32') + valid_b.astype('int32')
        values = np.where(valid_a, a, 0) + np.where(valid_b, b, 0)
        result = np.full(a.shape, -9999, dtype='float32')
        mask = count > 0
        result[mask] = values[mask] / count[mask]
        profile = reference.profile.copy()
        profile.update(dtype='float32', nodata=-9999, count=1)
        with rasterio.open(destination / 'mosaic.tif', 'w', **profile) as stream:
            stream.write(result, 1)
    summary = {'valid_count': int(mask.sum()), 'mean': float(result[mask].astype('float64').mean()) if mask.any() else None}
    (destination / 'summary.json').write_text(json.dumps(summary, allow_nan=False), encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    mosaic(args.input, args.output)
'''

ALTERNATE = r'''import argparse
import json
import math
from pathlib import Path
import numpy as np
import rasterio
from rasterio.warp import transform


def mosaic(source, destination):
    source = Path(source)
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    with rasterio.open(source / 'reference.tif') as reference, rasterio.open(source / 'secondary.tif') as other:
        a, b = reference.read(1), other.read(1)
        result = np.full(a.shape, -9999, dtype='float32')
        for row in range(reference.height):
            for col in range(reference.width):
                x, y = reference.xy(row, col)
                xx, yy = transform(reference.crs, other.crs, [x], [y])
                cc, rr = ~other.transform * (xx[0], yy[0])
                rr, cc = math.floor(rr), math.floor(cc)
                values = [float(a[row, col])] if a[row, col] != -9999 else []
                if 0 <= rr < other.height and 0 <= cc < other.width and b[rr, cc] != -9999:
                    values.append(float(b[rr, cc]))
                if values:
                    result[row, col] = sum(values) / len(values)
        profile = reference.profile.copy()
        profile.update(dtype='float32', nodata=-9999, count=1)
        with rasterio.open(destination / 'mosaic.tif', 'w', **profile) as stream:
            stream.write(result, 1)
    valid = result != -9999
    summary = {'valid_count': int(valid.sum()), 'mean': float(result[valid].astype('float64').mean()) if valid.any() else None}
    (destination / 'summary.json').write_text(json.dumps(summary, allow_nan=False), encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    mosaic(args.input, args.output)
'''

BUGGY = SOURCE.replace("valid_b = b != -9999", "valid_b = np.ones(b.shape, dtype=bool)")


def fixture(directory: Path, seed: int, level: int = 0) -> dict:
    try:
        import numpy as np
        import rasterio
        from rasterio.transform import from_origin
        from rasterio.warp import transform
    except ImportError as exc:
        raise RuntimeError("geospatial-raster requires pip install '.[geo]' (NumPy and rasterio)") from exc

    directory.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    size = 3 + min(level, 2)
    affine_a = from_origin(-97.0, 32.0, .01, .01)
    origin_x, origin_y = transform("EPSG:4326", "EPSG:3857", [-97.0], [32.0])
    affine_b = from_origin(origin_x[0] - 300, origin_y[0] + 450, 950, 1250)
    a = rng.integers(2, 60, size=(size, size)).astype("float32")
    b = rng.integers(-10, 40, size=(size + 1, size + 1)).astype("float32")
    a[0, 0] = -9999
    a[-1, -1] = -9999
    b[0, 0] = -9999
    b[1, 1] = -9999
    if seed % 3 == 0:
        b[:] = -9999
    if seed % 5 == 0:
        a[:] = -9999
        b[:] = -9999
    for name, data, affine, crs in (("reference.tif", a, affine_a, "EPSG:4326"),
                                    ("secondary.tif", b, affine_b, "EPSG:3857")):
        with rasterio.open(directory / name, "w", driver="GTiff", count=1, dtype="float32", nodata=-9999,
                           width=data.shape[1], height=data.shape[0], transform=affine, crs=crs) as stream:
            stream.write(data, 1)
    # Independent expected values: analytic Web Mercator target centers and
    # affine inversion, not rasterio.warp.reproject from the reference solution.
    expected = []
    for row in range(size):
        line = []
        for col in range(size):
            lon, lat = affine_a * (col + .5, row + .5)
            x = 6378137.0 * math.radians(lon)
            y = 6378137.0 * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))
            source_col, source_row = ~affine_b * (x, y)
            source_col, source_row = math.floor(source_col), math.floor(source_row)
            values = [float(a[row, col])] if a[row, col] != -9999 else []
            if 0 <= source_row < b.shape[0] and 0 <= source_col < b.shape[1]:
                value = float(b[source_row, source_col])
                if value != -9999:
                    values.append(value)
            line.append(float(np.float32(sum(values) / len(values))) if values else -9999)
        expected.append(line)
    valid = [value for line in expected for value in line if value != -9999]
    return {"pixels": expected, "crs": "EPSG:4326", "transform": list(affine_a),
            "summary": {"valid_count": len(valid), "mean": sum(valid) / len(valid) if valid else None}}
