"""Score raster to candidate polygons (FR-082), with the per-polygon masking report (FR-052).

Pixels at or above the threshold become 8-connected polygons; polygons below the
minimum mapping unit are removed. Each polygon gets its mean model score, a score
band (never a raw score in the interface, FRD 10.3), its area, and, when the prepared
scene is given, the share of it the quality mask removed. Scores are model scores,
not probabilities (FR-084).
"""
from __future__ import annotations
from pathlib import Path

import numpy as np

SCORE_BANDS = ("low", "medium", "high")


def score_band(mean: float, threshold: float) -> str:
    """Thirds of the range above the threshold. Bands are for display only, not calibrated."""
    span = max(1.0 - threshold, 1e-6)
    i = min(int(round((mean - threshold) / span * 3, 9)), 2)   # rounding keeps exact band edges in the upper band
    return SCORE_BANDS[max(i, 0)]


def vectorise(score_path, threshold: float, mmu_ha: float = 1.0, prepared_dir=None):
    """Polygons (EPSG:4326 GeoDataFrame) where score >= threshold (scores in [0, 1], or uint8 levels / 255)."""
    import geopandas as gpd
    import rasterio
    from rasterio.features import rasterize, shapes
    from shapely.geometry import shape
    with rasterio.open(score_path) as src:
        raw = src.read(1)
        tr, crs = src.transform, src.crs
        px_ha = abs(tr.a * tr.e) / 1e4
    score = raw.astype("float32") / 255.0 if raw.dtype == np.uint8 else raw.astype("float32")
    valid = np.ones(score.shape, bool)
    if prepared_dir is not None:
        with rasterio.open(Path(prepared_dir) / "reflectance.tif") as s:
            valid = np.isfinite(s.read(1))
    mask = (score >= threshold) & valid
    polys = [shape(g) for g, v in shapes(mask.astype("uint8"), mask=mask, transform=tr, connectivity=8) if v == 1]
    polys = [p for p in polys if p.area / 1e4 >= mmu_ha] if crs.is_projected else polys
    cols = ["area_ha", "score_mean", "score_band", "threshold", "min_mapping_unit_ha"]
    if not polys:
        return gpd.GeoDataFrame({c: [] for c in cols}, geometry=[], crs=4326)
    ids = rasterize([(p, i + 1) for i, p in enumerate(polys)], out_shape=score.shape, transform=tr, dtype="int32")
    sums = np.bincount(ids.ravel(), weights=score.ravel(), minlength=len(polys) + 1)
    counts = np.bincount(ids.ravel(), minlength=len(polys) + 1)
    means = sums[1:] / np.maximum(counts[1:], 1)
    g = gpd.GeoDataFrame({"area_ha": [p.area / 1e4 for p in polys], "score_mean": means.astype(float),
                          "score_band": [score_band(m, threshold) for m in means], "threshold": threshold,
                          "min_mapping_unit_ha": mmu_ha}, geometry=polys, crs=crs)
    if prepared_dir is not None:
        from .labels import polygon_mask_report
        rep = polygon_mask_report(prepared_dir, g)
        g["masked_fraction"] = rep["masked_fraction"].fillna(1.0).values
        g["mask_status"] = rep["mask_status"].values
    return g.to_crs(4326).reset_index(drop=True)
