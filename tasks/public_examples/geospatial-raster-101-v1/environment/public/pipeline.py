import argparse
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
        valid_b = np.ones(b.shape, dtype=bool)
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
