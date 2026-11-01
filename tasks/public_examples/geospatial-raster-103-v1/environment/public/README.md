Repair /app/pipeline.py to mosaic two synthetic GeoTIFFs. Accept
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
