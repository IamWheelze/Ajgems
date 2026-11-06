"""Label loading and rasterising, plus a reader for the Global ML-ready file."""
from __future__ import annotations
from pathlib import Path
import numpy as np

# Global ML-ready v1.0.0, checked against the released file on 2026-11-03: the `tiles`
# layer has these columns; label layers have only tile_id and geometry.
EXPECTED_COLUMNS = {"tiles": {"tile_id", "s2_tile_id", "split"}}
EXPECTED_LABEL_COLUMNS = {"tile_id"}


def read_vectors(path, layer=None):
    import geopandas as gpd
    return gpd.read_file(path, layer=layer) if layer else gpd.read_file(path)


def rasterize_to(raster_path, gdf) -> np.ndarray:
    """Burn polygons onto a raster's grid. Reprojects labels to the raster's CRS."""
    import rasterio
    from rasterio.features import rasterize
    with rasterio.open(raster_path) as src:
        shapes = [(g, 1) for g in gdf.to_crs(src.crs).geometry if g is not None and not g.is_empty]
        if not shapes:
            return np.zeros((src.height, src.width), "uint8")
        return rasterize(shapes, out_shape=(src.height, src.width), transform=src.transform, dtype="uint8")


def inspect_dataset(path) -> dict:
    """Report what a dataset file actually contains, before any code relies on it."""
    import pyogrio
    report = {"path": str(path), "layers": {}}
    for name, _ in pyogrio.list_layers(path):
        info = pyogrio.read_info(path, layer=name)
        cols = set(info["fields"].tolist())
        report["layers"][name] = {"features": int(info["features"]), "crs": info["crs"],
                                  "columns": sorted(cols),
                                  "missing_expected": sorted(EXPECTED_COLUMNS.get(name, EXPECTED_LABEL_COLUMNS) - cols)}
    return report


def audit_labels(prepared_dir, labels_path) -> dict:
    """Mask rates inside versus outside the labels for one prepared scene."""
    import rasterio
    from .quality import mask_rates
    prepared_dir = Path(prepared_dir)
    inside = rasterize_to(prepared_dir / "reflectance.tif", read_vectors(labels_path))
    with rasterio.open(prepared_dir / "scl.tif") as src:
        scl = src.read(1)
    return {"scene": prepared_dir.name, **mask_rates(scl, inside)}


def polygon_mask_report(prepared_dir, polygons):
    """Add masking columns to each polygon for one prepared scene (see quality.polygon_coverage).

    Works for model candidates, labels or areas of interest. Returns a copy of the
    polygons, in their own CRS, with pixels, usable_fraction, masked_fraction,
    cloud_fraction, cloud_medium_fraction, shadow_fraction, nodata_fraction,
    snow_fraction, mask_status and scene_id. Polygons are measured one at a time,
    so overlapping polygons are each counted in full.
    """
    import json
    import rasterio
    from rasterio.features import geometry_mask
    from rasterio.errors import WindowError
    from rasterio.windows import Window, from_bounds
    from .quality import polygon_coverage
    prepared_dir = Path(prepared_dir)
    gdf = read_vectors(polygons) if not hasattr(polygons, "geometry") else polygons
    scene_id = json.loads((prepared_dir / "prepared.json").read_text()).get("scene_id", prepared_dir.name)
    rows = []
    with rasterio.open(prepared_dir / "scl.tif") as src:
        geoms = gdf.to_crs(src.crs).geometry
        full = Window(0, 0, src.width, src.height)
        for g in geoms:
            vals = np.array([], "uint8")
            if g is not None and not g.is_empty:
                w = from_bounds(*g.bounds, transform=src.transform)
                c0, r0 = int(np.floor(w.col_off)), int(np.floor(w.row_off))
                c1, r1 = int(np.ceil(w.col_off + w.width)), int(np.ceil(w.row_off + w.height))
                try:
                    win = Window(c0, r0, max(c1 - c0, 1), max(r1 - r0, 1)).intersection(full)
                except WindowError:  # polygon entirely outside the image
                    win = None
                if win is not None and win.width > 0 and win.height > 0:
                    scl = src.read(1, window=win)
                    t = src.window_transform(win)
                    inside = ~geometry_mask([g], scl.shape, t, all_touched=False)
                    if not inside.any():  # polygon smaller than a pixel: count the pixels it touches
                        inside = ~geometry_mask([g], scl.shape, t, all_touched=True)
                    vals = scl[inside]
            rows.append(polygon_coverage(vals))
    out = gdf.copy()
    for k in rows[0] if rows else []:
        out[k] = [r[k] for r in rows]
    out["scene_id"] = scene_id
    return out


def summarise_mask_report(report) -> dict:
    """Totals over a polygon mask report, pixel-weighted."""
    n = report["pixels"].fillna(0)
    total = int(n.sum())
    w = lambda c: float((report[c].fillna(0) * n).sum() / total) if total else None
    return {"polygons": int(len(report)), "pixels": total,
            "by_status": {k: int(v) for k, v in report["mask_status"].value_counts().items()},
            "masked_fraction": w("masked_fraction"), "cloud_fraction": w("cloud_fraction")}
