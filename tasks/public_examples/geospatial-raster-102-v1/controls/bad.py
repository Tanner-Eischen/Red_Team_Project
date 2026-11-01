import argparse
from pathlib import Path
import subprocess
import sys

parser = argparse.ArgumentParser()
parser.add_argument('--workspace', type=Path, required=True)
args = parser.parse_args()
args.workspace.mkdir(parents=True, exist_ok=True)
target = args.workspace / 'pipeline.py'
target.write_text("import argparse\nimport json\nfrom pathlib import Path\nimport numpy as np\nimport rasterio\nfrom rasterio.warp import reproject, Resampling\n\n\ndef mosaic(source, destination):\n    source = Path(source)\n    destination = Path(destination)\n    destination.mkdir(parents=True, exist_ok=True)\n    with rasterio.open(source / 'reference.tif') as reference, rasterio.open(source / 'secondary.tif') as other:\n        a = reference.read(1)\n        b = np.full(a.shape, -9999, dtype='float32')\n        reproject(source=rasterio.band(other, 1), destination=b,\n                  src_transform=other.transform, src_crs=other.crs,\n                  dst_transform=reference.transform, dst_crs=reference.crs,\n                  src_nodata=-9999, dst_nodata=-9999, resampling=Resampling.nearest)\n        valid_a = a != -9999\n        valid_b = np.ones(b.shape, dtype=bool)\n        count = valid_a.astype('int32') + valid_b.astype('int32')\n        values = np.where(valid_a, a, 0) + np.where(valid_b, b, 0)\n        result = np.full(a.shape, -9999, dtype='float32')\n        mask = count > 0\n        result[mask] = values[mask] / count[mask]\n        profile = reference.profile.copy()\n        profile.update(dtype='float32', nodata=-9999, count=1)\n        with rasterio.open(destination / 'mosaic.tif', 'w', **profile) as stream:\n            stream.write(result, 1)\n    summary = {'valid_count': int(mask.sum()), 'mean': float(result[mask].astype('float64').mean()) if mask.any() else None}\n    (destination / 'summary.json').write_text(json.dumps(summary, allow_nan=False), encoding='utf-8')\n\n\nif __name__ == '__main__':\n    parser = argparse.ArgumentParser()\n    parser.add_argument('--input', required=True)\n    parser.add_argument('--output', required=True)\n    args = parser.parse_args()\n    mosaic(args.input, args.output)\n", encoding='utf-8')
result = subprocess.run([sys.executable, str(target), '--input',
                         str(args.workspace / 'input'), '--output',
                         str(args.workspace / 'output')], check=False)
raise SystemExit(result.returncode)
