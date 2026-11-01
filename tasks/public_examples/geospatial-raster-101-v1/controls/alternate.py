import argparse
from pathlib import Path
import subprocess
import sys

parser = argparse.ArgumentParser()
parser.add_argument('--workspace', type=Path, required=True)
args = parser.parse_args()
args.workspace.mkdir(parents=True, exist_ok=True)
target = args.workspace / 'pipeline.py'
target.write_text("import argparse\nimport json\nimport math\nfrom pathlib import Path\nimport numpy as np\nimport rasterio\nfrom rasterio.warp import transform\n\n\ndef mosaic(source, destination):\n    source = Path(source)\n    destination = Path(destination)\n    destination.mkdir(parents=True, exist_ok=True)\n    with rasterio.open(source / 'reference.tif') as reference, rasterio.open(source / 'secondary.tif') as other:\n        a, b = reference.read(1), other.read(1)\n        result = np.full(a.shape, -9999, dtype='float32')\n        for row in range(reference.height):\n            for col in range(reference.width):\n                x, y = reference.xy(row, col)\n                xx, yy = transform(reference.crs, other.crs, [x], [y])\n                cc, rr = ~other.transform * (xx[0], yy[0])\n                rr, cc = math.floor(rr), math.floor(cc)\n                values = [float(a[row, col])] if a[row, col] != -9999 else []\n                if 0 <= rr < other.height and 0 <= cc < other.width and b[rr, cc] != -9999:\n                    values.append(float(b[rr, cc]))\n                if values:\n                    result[row, col] = sum(values) / len(values)\n        profile = reference.profile.copy()\n        profile.update(dtype='float32', nodata=-9999, count=1)\n        with rasterio.open(destination / 'mosaic.tif', 'w', **profile) as stream:\n            stream.write(result, 1)\n    valid = result != -9999\n    summary = {'valid_count': int(valid.sum()), 'mean': float(result[valid].astype('float64').mean()) if valid.any() else None}\n    (destination / 'summary.json').write_text(json.dumps(summary, allow_nan=False), encoding='utf-8')\n\n\nif __name__ == '__main__':\n    parser = argparse.ArgumentParser()\n    parser.add_argument('--input', required=True)\n    parser.add_argument('--output', required=True)\n    args = parser.parse_args()\n    mosaic(args.input, args.output)\n", encoding='utf-8')
result = subprocess.run([sys.executable, str(target), '--input',
                         str(args.workspace / 'input'), '--output',
                         str(args.workspace / 'output')], check=False)
raise SystemExit(result.returncode)
