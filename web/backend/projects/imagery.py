"""Scene previews for the map: a display layer warped to WGS 84 as PNG, cached on disk.

Uses minefoot's fixed stretches (FR-122), so the web map shows the same colours as the
gallery and QGIS. Unusable pixels stay dark grey, never clean ground (FRD 10.3). A full
raster tile server (TiTiler, FRD 11.1) replaces this once jobs produce large mosaics.
"""
from pathlib import Path

import numpy as np
from django.conf import settings

LAYERS = ("true_colour", "swir_false_colour", "nir_false_colour", "ndvi", "ndwi", "quality", "not_assessed", "score")
# FR-124 heatmap: one hue, light to dark with rising model score; scores under SCORE_FLOOR stay transparent.
SCORE_RAMP = ((253, 217, 181), (166, 54, 3))
SCORE_FLOOR = 0.1


def not_assessed_rgba(scl):
    """Diagonal hatch where the quality mask removed pixels (FR-125); transparent elsewhere."""
    from minefoot.quality import usable_mask
    h, w = scl.shape
    yy, xx = np.mgrid[:h, :w]
    stripe = ((xx + yy) % 6) < 2
    out = np.zeros((h, w, 4), "uint8")
    masked = ~usable_mask(scl)
    out[masked & stripe] = (235, 235, 235, 230)
    out[masked & ~stripe] = (60, 60, 60, 90)
    return out


MAX_SIZE = 2048


def score_rgba(levels):
    """Model score levels (uint8, 0..255) as a single-hue heatmap; alpha grows with the score."""
    s = levels.astype("float32") / 255.0
    lo, hi = np.array(SCORE_RAMP[0], "float32"), np.array(SCORE_RAMP[1], "float32")
    rgb = (lo + (hi - lo) * s[..., None]).astype("uint8")
    alpha = np.where(s < SCORE_FLOOR, 0, 50 + 150 * s).astype("uint8")
    return np.dstack([rgb, alpha])


def score_paths(tiles):
    """Score maps written by the job for these tiles (job folder/<grid_id>_score.tif)."""
    out = []
    for t in tiles:
        p = Path(t.score_path) if t.score_path else (Path(t.prepared_path).parents[1] / f"{t.grid_id}_score.tif"
                                                      if t.prepared_path else None)   # jobs run before score_path existed
        if p is not None and p.exists():
            out.append(p)
    return out


def _render(arr, scl, t, crs, layer, cache):
    """Display layer (or the not-assessed hatch) warped to WGS 84 as RGBA PNG; returns (path, bounds)."""
    import rasterio
    from PIL import Image
    from rasterio.warp import Resampling, calculate_default_transform, reproject
    from minefoot.viewer import build_layers
    shape = scl.shape
    if layer in ("not_assessed", "score"):
        rgba = not_assessed_rgba(scl) if layer == "not_assessed" else score_rgba(arr)
        img, alpha_src = rgba[..., :3], rgba[..., 3]
    else:
        img, alpha_src = build_layers(arr, scl)[layer], None              # (H, W, 3) uint8
    dst_t, w, h = calculate_default_transform(crs, "EPSG:4326", shape[1], shape[0],
                                              *rasterio.transform.array_bounds(shape[0], shape[1], t))
    out = np.zeros((4, h, w), "uint8")
    for b in range(3):
        reproject(img[..., b], out[b], src_transform=t, src_crs=crs, dst_transform=dst_t, dst_crs="EPSG:4326",
                  resampling=Resampling.nearest)
    alpha = alpha_src if alpha_src is not None else np.full(shape, 255, "uint8")
    reproject(alpha, out[3], src_transform=t, src_crs=crs, dst_transform=dst_t, dst_crs="EPSG:4326",
              resampling=Resampling.nearest, dst_nodata=0)
    out[3][out[3] <= 1] = 0   # reproject writes 1 for source zeros next to nodata; drawn alphas are always far above 1
    cache.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(out.transpose(1, 2, 0), "RGBA").save(cache, optimize=True)
    west, north = dst_t * (0, 0); east, south = dst_t * (w, h)
    bounds = [west, south, east, north]
    cache.with_suffix(".bounds").write_text(",".join(str(v) for v in bounds))
    return cache, bounds


def _cached(cache):
    meta = cache.with_suffix(".bounds")
    if cache.exists() and meta.exists():
        return cache, [float(v) for v in meta.read_text().split(",")]
    return None


def _read(prepared_dirs):
    """Reflectance and SCL from one or more prepared folders (job tiles), mosaicked and downsampled."""
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.merge import merge
    srcs = [rasterio.open(Path(d) / "reflectance.tif") for d in prepared_dirs]
    scls = [rasterio.open(Path(d) / "scl.tif") for d in prepared_dirs]
    try:
        crs = srcs[0].crs
        keep = [i for i, s in enumerate(srcs) if s.crs == crs]   # tiles in another UTM zone are left out
        srcs, scls = [srcs[i] for i in keep], [scls[i] for i in keep]
        W = max(s.bounds.right for s in srcs) - min(s.bounds.left for s in srcs)
        H = max(s.bounds.top for s in srcs) - min(s.bounds.bottom for s in srcs)
        res = max(abs(srcs[0].res[0]), max(W, H) / MAX_SIZE)
        arr, t = merge(srcs, res=res, resampling=Resampling.average, nodata=np.nan)
        scl, _ = merge(scls, res=res, resampling=Resampling.nearest, nodata=0)
        return arr, scl[0], t, crs
    finally:
        for s in srcs + scls:
            s.close()


def _read_scores(paths):
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.merge import merge
    srcs = [rasterio.open(p) for p in paths]
    try:
        crs = srcs[0].crs
        srcs = [s for s in srcs if s.crs == crs]
        W = max(s.bounds.right for s in srcs) - min(s.bounds.left for s in srcs)
        H = max(s.bounds.top for s in srcs) - min(s.bounds.bottom for s in srcs)
        res = max(abs(srcs[0].res[0]), max(W, H) / MAX_SIZE)
        arr, t = merge(srcs, res=res, resampling=Resampling.nearest, nodata=0)
        return arr[0], t, crs
    finally:
        for s in srcs:
            s.close()


def render_scene_png(scene, layer="true_colour"):
    if layer == "score":
        raise ValueError("no score map is stored for this candidate")
    if layer not in LAYERS:
        raise ValueError(f"layer must be one of {LAYERS}")
    prep = Path(scene.prepared_path)
    if not (prep / "reflectance.tif").exists():
        raise ValueError("this scene's prepared imagery is not on the server")
    cache = settings.MINEFOOT["IMAGE_CACHE"] / f"scene_{scene.pk}_{layer}.png"
    hit = _cached(cache)
    if hit:
        return hit
    arr, scl, t, crs = _read([prep])
    return _render(arr, scl, t, crs, layer, cache)


def render_detection_png(detection, layer="true_colour"):
    """The imagery under a detection: a mosaic of its job's tiles that touch it, or its scene's folder."""
    if layer not in LAYERS:
        raise ValueError(f"layer must be one of {LAYERS}")
    tiles = [t for t in detection.job.tiles.filter(geometry__intersects=detection.geometry).exclude(prepared_path="")
             if (Path(t.prepared_path) / "reflectance.tif").exists()]
    if not tiles:
        return render_scene_png(detection.scene, layer)
    key = "_".join(sorted(str(t.pk) for t in tiles))
    cache = settings.MINEFOOT["IMAGE_CACHE"] / f"tiles_{key}_{layer}.png"
    hit = _cached(cache)
    if hit:
        return hit
    if layer == "score":
        paths = score_paths(tiles)
        if not paths:
            raise ValueError("no score map is stored for this candidate")
        levels, t, crs = _read_scores(paths)
        return _render(levels, levels, t, crs, "score", cache)
    arr, scl, t, crs = _read([t.prepared_path for t in tiles])
    return _render(arr, scl, t, crs, layer, cache)


SCL_NAMES = {0: "no data", 1: "saturated or defective", 2: "dark area", 3: "cloud shadow", 4: "vegetation", 5: "not vegetated",
             6: "water", 7: "unclassified", 8: "cloud, medium", 9: "cloud, high", 10: "thin cirrus", 11: "snow or ice"}


def inspect_pixel(detection, lon, lat) -> dict:
    """FR-128: band reflectances, indices, mask flags, model score and source scene at one point, read
    straight from the job tile's rasters (no resampling)."""
    import rasterio
    from rasterio.warp import transform as warp
    from django.contrib.gis.geos import Point
    from minefoot.quality import NOT_USABLE, SUSPECT_BRIGHT
    from minefoot.schema import BANDS
    pt = Point(lon, lat, srid=4326)
    tile = (detection.job.tiles.filter(geometry__intersects=pt).exclude(prepared_path="").select_related("scene").first())
    if tile is None or not (Path(tile.prepared_path) / "reflectance.tif").exists():
        raise ValueError("no job imagery is stored at this point")
    prep = Path(tile.prepared_path)
    with rasterio.open(prep / "reflectance.tif") as src:
        (x,), (y,) = warp("EPSG:4326", src.crs, [lon], [lat])
        row, col = src.index(x, y)
        if not (0 <= row < src.height and 0 <= col < src.width):
            raise ValueError("this point is outside the tile's imagery")
        win = rasterio.windows.Window(col, row, 1, 1)
        vals = src.read(window=win)[:, 0, 0].astype(float)
        crs = str(src.crs)
    with rasterio.open(prep / "scl.tif") as s:
        scl = int(s.read(1, window=win)[0, 0])
    refl = {b: (None if not np.isfinite(v) else round(float(v), 4)) for b, v in zip(BANDS, vals[:len(BANDS)])}
    nd = lambda a, b: (None if refl[a] is None or refl[b] is None or refl[a] + refl[b] == 0
                       else round((refl[a] - refl[b]) / (refl[a] + refl[b]), 4))
    score = None
    paths = score_paths([tile])
    if paths:
        with rasterio.open(paths[0]) as s:
            r2, c2 = s.index(x, y)
            if 0 <= r2 < s.height and 0 <= c2 < s.width:
                score = round(int(s.read(1, window=rasterio.windows.Window(c2, r2, 1, 1))[0, 0]) / 255, 3)
    sc = tile.scene
    return {"lon": lon, "lat": lat, "crs": crs, "row": int(row), "col": int(col), "tile": tile.grid_id,
            "reflectance": refl, "ndvi": nd("B08", "B04"), "ndwi": nd("B03", "B08"),
            "scl": scl, "scl_class": SCL_NAMES.get(scl, "unknown"), "usable": scl not in NOT_USABLE,
            "suspect_bright": scl in SUSPECT_BRIGHT, "model_score": score,
            "threshold": detection.job.parameters.get("threshold", detection.threshold),
            "scene": {"id": sc.source_id, "provider": sc.provider, "date": sc.acquired_at.date().isoformat(),
                      "processing_baseline": sc.processing_baseline} if sc else None}
