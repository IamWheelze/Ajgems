"""Web-map raster tiles for a job (the FRD's TiTiler role, built on rio-tiler, TiTiler's core).

Each 256 px Web Mercator tile reads only the windows it needs from the job tiles' stored rasters (reflectance,
scene classes, model score), mosaics them in a fixed order, and colours them with minefoot's fixed stretches
(viewer.build_layers), so a tile looks exactly like the gallery, the PNG previews and QGIS. Unusable pixels keep
the dark "no data" colour or the not-assessed hatch; they never look like clean ground. Tiles are cached on disk.
"""
import io
from pathlib import Path

import numpy as np
from django.conf import settings

from .imagery import LAYERS, SCORE_FLOOR, not_assessed_rgba, score_rgba

TILE = 256
MIN_ZOOM, MAX_ZOOM = 10, 17   # below 10 a tile spans many job tiles at full resolution; the PNG overview serves there


def _tile_bounds(z, x, y):
    import morecantile
    b = morecantile.tms.get("WebMercatorQuad").bounds(morecantile.Tile(x, y, z))
    return b.left, b.bottom, b.right, b.top


def _candidates(job, z, x, y):
    from django.contrib.gis.geos import Polygon
    w, s, e, n = _tile_bounds(z, x, y)
    box = Polygon.from_bbox((w, s, e, n)); box.srid = 4326
    return [t for t in job.tiles.filter(geometry__intersects=box).exclude(prepared_path="").order_by("grid_id")
            if (Path(t.prepared_path) / "reflectance.tif").exists()]


def _read(path, z, x, y, indexes=None, nodata=None):
    from rio_tiler.errors import TileOutsideBounds
    from rio_tiler.io import Reader
    try:
        with Reader(str(path)) as r:
            img = r.tile(x, y, z, tilesize=TILE, indexes=indexes, resampling_method="nearest", nodata=nodata)
        return img.data, ~img.mask.astype(bool) if img.mask.ndim == 2 else img.mask
    except TileOutsideBounds:
        return None, None


def render(job, layer, z, x, y) -> bytes | None:
    """PNG bytes for one map tile, or None when the job has no imagery there (the map then shows nothing)."""
    from PIL import Image
    if layer not in LAYERS:
        raise ValueError(f"layer must be one of {LAYERS}")
    if not MIN_ZOOM <= z <= MAX_ZOOM:
        return None
    cache = Path(settings.MINEFOOT["IMAGE_CACHE"]) / "xyz" / f"job{job.pk}" / layer / str(z) / str(x) / f"{y}.png"
    if cache.exists():
        return cache.read_bytes()
    tiles = _candidates(job, z, x, y)
    if not tiles:
        return None
    arr = np.full((10, TILE, TILE), np.nan, "float32"); scl = np.zeros((TILE, TILE), "uint8"); lv = np.zeros((TILE, TILE), "uint8")
    have = np.zeros((TILE, TILE), bool)
    for t in tiles:   # fixed order: the first tile with data wins each pixel, so tiles never flicker between zooms
        prep = Path(t.prepared_path)
        q, _ = _read(prep / "scl.tif", z, x, y, indexes=(1,), nodata=0)
        if q is None:
            continue
        q = q[0]
        new = (q > 0) & ~have
        if not new.any():
            continue
        if layer == "score":
            if t.score_path and Path(t.score_path).exists():
                s, _ = _read(t.score_path, z, x, y, indexes=(1,), nodata=None)
                if s is not None:
                    lv[new] = s[0][new]
        else:
            a, _ = _read(prep / "reflectance.tif", z, x, y)
            if a is None:
                continue
            arr[:, new] = a[:, new]
        scl[new] = q[new]; have |= new
    if not have.any():
        return None
    if layer == "score":
        rgba = score_rgba(lv)
    elif layer == "not_assessed":
        rgba = not_assessed_rgba(scl)
    else:
        from minefoot.viewer import build_layers
        rgb = build_layers(arr, scl)[layer]
        rgba = np.dstack([rgb, np.where(have, 255, 0).astype("uint8")])
    rgba[~have] = 0
    buf = io.BytesIO(); Image.fromarray(rgba, "RGBA").save(buf, "PNG", optimize=True)
    cache.parent.mkdir(parents=True, exist_ok=True); cache.write_bytes(buf.getvalue())
    return buf.getvalue()
