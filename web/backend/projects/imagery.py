"""Scene previews for the map: a display layer warped to WGS 84 as PNG, cached on disk.

Uses minefoot's fixed stretches (FR-122), so the web map shows the same colours as the
gallery and QGIS. Unusable pixels stay dark grey, never clean ground (FRD 10.3). A full
raster tile server (TiTiler, FRD 11.1) replaces this once jobs produce large mosaics.
"""
from pathlib import Path

import numpy as np
from django.conf import settings

LAYERS = ("true_colour", "swir_false_colour", "nir_false_colour", "ndvi", "ndwi", "quality")
MAX_SIZE = 2048


def render_scene_png(scene, layer="true_colour"):
    import rasterio
    from PIL import Image
    from rasterio.warp import Resampling, calculate_default_transform, reproject
    from minefoot.viewer import build_layers
    if layer not in LAYERS:
        raise ValueError(f"layer must be one of {LAYERS}")
    prep = Path(scene.prepared_path)
    if not (prep / "reflectance.tif").exists():
        raise ValueError("this scene's prepared imagery is not on the server")
    cache = settings.MINEFOOT["IMAGE_CACHE"] / f"scene_{scene.pk}_{layer}.png"
    meta = cache.with_suffix(".bounds")
    if cache.exists() and meta.exists():
        return cache, [float(v) for v in meta.read_text().split(",")]
    with rasterio.open(prep / "reflectance.tif") as src:
        f = max(src.width, src.height) / MAX_SIZE
        shape = (int(src.height / max(f, 1)), int(src.width / max(f, 1)))
        arr = src.read(out_shape=(src.count, *shape), resampling=Resampling.average)
        t = src.transform * src.transform.scale(src.width / shape[1], src.height / shape[0])
        crs = src.crs
    with rasterio.open(prep / "scl.tif") as s:
        scl = s.read(1, out_shape=shape, resampling=Resampling.nearest)
    img = build_layers(arr, scl)[layer]                               # (H, W, 3) uint8
    dst_t, w, h = calculate_default_transform(crs, "EPSG:4326", shape[1], shape[0],
                                              *rasterio.transform.array_bounds(shape[0], shape[1], t))
    out = np.zeros((4, h, w), "uint8")
    for b in range(3):
        reproject(img[..., b], out[b], src_transform=t, src_crs=crs, dst_transform=dst_t, dst_crs="EPSG:4326",
                  resampling=Resampling.nearest)
    alpha = np.full(shape, 255, "uint8")
    reproject(alpha, out[3], src_transform=t, src_crs=crs, dst_transform=dst_t, dst_crs="EPSG:4326",
              resampling=Resampling.nearest, dst_nodata=0)
    cache.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(out.transpose(1, 2, 0), "RGBA").save(cache, optimize=True)
    west, north = dst_t * (0, 0); east, south = dst_t * (w, h)
    bounds = [west, south, east, north]
    meta.write_text(",".join(str(v) for v in bounds))
    return cache, bounds
