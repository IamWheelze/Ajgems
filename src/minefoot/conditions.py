"""Condition flags for review (FR-054): haze or smoke, burn scars and water (flooding risk).

These are review prompts, not masks: they never remove pixels, and they never change a score. Each is a
simple spectral rule on usable pixels, chosen to catch conditions that make mining footprints look
different or that mimic them:
- haze or smoke: a high haze-optimised transform (HOT = B2 - 0.5 x B4) over vegetated ground;
- burn scar: dark ground with low NIR and a negative normalised burn ratio, away from water;
- water: NDWI above 0.1 (open water, flooded ground, pit lakes).
Thresholds are provisional. How well each rule agrees with labelled references is measured by evaluate().
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from .quality import usable_mask
from .schema import BANDS

HOT_MIN, NDVI_HAZE_MIN = 0.05, 0.3
BURN_NBR_MAX, BURN_NIR_MAX, BURN_RED_MAX = 0.0, 0.15, 0.10
WATER_NDWI_MIN = 0.1
TILE_FLAG_AT = {"haze_or_smoke": 0.20, "burn_scar": 0.10, "water": 0.02}       # share of usable pixels
POLYGON_FLAG_AT = {"haze_or_smoke": 0.30, "burn_scar": 0.30, "water": 0.30}    # share of the polygon's usable pixels
MESSAGES = {"haze_or_smoke": "haze or smoke: colours and edges may be muted",
            "burn_scar": "possible burn scar: dark burnt ground can look like disturbance",
            "water": "water present: check for flooding, pit lakes or a riverbank"}


def _b(arr, name):
    return arr[list(BANDS).index(name)].astype("float32")


def _nd(a, b):
    with np.errstate(invalid="ignore", divide="ignore"):
        return (a - b) / (a + b)


def masks(arr: np.ndarray, scl: np.ndarray) -> dict[str, np.ndarray]:
    """Boolean masks of each condition on usable pixels, plus the usable mask itself."""
    b2, b3, b4, b8, b12 = (_b(arr, n) for n in ("B02", "B03", "B04", "B08", "B12"))
    valid = usable_mask(scl) & np.isfinite(arr).all(axis=0)
    ndvi, ndwi, nbr = _nd(b8, b4), _nd(b3, b8), _nd(b8, b12)
    water = valid & (ndwi > WATER_NDWI_MIN)
    haze = valid & (b2 - 0.5 * b4 > HOT_MIN) & (ndvi > NDVI_HAZE_MIN)
    burn = valid & ~water & (nbr < BURN_NBR_MAX) & (b8 < BURN_NIR_MAX) & (b4 < BURN_RED_MAX)
    return {"valid": valid, "haze_or_smoke": haze, "burn_scar": burn, "water": water}


def _summarise(m, within=None, flag_at=TILE_FLAG_AT):
    sel = m["valid"] if within is None else (m["valid"] & within)
    n = int(sel.sum())
    share = {k: (float((m[k] & sel).sum()) / n if n else 0.0) for k in flag_at}
    return {"usable_pixels": n, "share": {k: round(v, 4) for k, v in share.items()},
            "flags": [{"condition": k, "share": round(v, 3), "message": MESSAGES[k]} for k, v in share.items() if n and v >= flag_at[k]]}


def tile_flags(prepared_dir) -> dict:
    import rasterio
    with rasterio.open(Path(prepared_dir) / "reflectance.tif") as s, rasterio.open(Path(prepared_dir) / "scl.tif") as q:
        return _summarise(masks(s.read(), q.read(1)))


def polygon_flags(prepared_dir, polygon_wgs84) -> dict:
    """Flags for one candidate: shares of its usable pixels (all_touched=False, the same rule as the masking report)."""
    import geopandas as gpd
    import rasterio
    from rasterio.features import geometry_mask
    with rasterio.open(Path(prepared_dir) / "reflectance.tif") as s, rasterio.open(Path(prepared_dir) / "scl.tif") as q:
        arr, scl, tr, crs = s.read(), q.read(1), s.transform, s.crs
    g = gpd.GeoSeries([polygon_wgs84], crs=4326).to_crs(crs).iloc[0]
    inside = ~geometry_mask([g], out_shape=scl.shape, transform=tr)
    return _summarise(masks(arr, scl), within=inside, flag_at=POLYGON_FLAG_AT)


def evaluate(prepared_root, worldcover_root, site_ids, out=None, labels_root=None) -> dict:
    """Measured agreement with labelled references on held-out sites.
    water vs ESA WorldCover classes 80 (water) and 90 (wetland); haze vs Sen2Cor's thin-cirrus class (SCL 10) on
    pixels the mask keeps would be circular, so haze is compared on all non-cloud pixels with cirrus as reference.
    Burn scars have no labelled reference here: only how often the rule fires is reported."""
    import rasterio
    tot = {"water": [0, 0, 0], "haze": [0, 0, 0]}   # tp, fp, fn
    burn_pix = valid_pix = 0
    on_mining = {k: [0, 0] for k in ("haze_or_smoke", "burn_scar", "water")}    # flagged, total labelled mining pixels
    off_mining = {k: [0, 0] for k in on_mining}
    sites_flagged = {k: 0 for k in TILE_FLAG_AT}
    used = []
    for sid in site_ids:
        p, wc_path = Path(prepared_root) / sid, Path(worldcover_root) / f"{sid}.tif"
        if not (p / "reflectance.tif").exists() or not wc_path.exists():
            continue
        with rasterio.open(p / "reflectance.tif") as s, rasterio.open(p / "scl.tif") as q, rasterio.open(wc_path) as w:
            arr, scl, wc = s.read(), q.read(1), w.read(1)
        m = masks(arr, scl)
        ref = np.isin(wc, (80, 90)) & m["valid"]
        tot["water"][0] += int((m["water"] & ref).sum()); tot["water"][1] += int((m["water"] & ~ref).sum())
        tot["water"][2] += int((~m["water"] & ref).sum())
        # Haze: rule applied to non-cloud pixels (cirrus kept), reference = SCL thin cirrus.
        b2, b4, b8 = _b(arr, "B02"), _b(arr, "B04"), _b(arr, "B08")
        noncloud = ~np.isin(scl, (0, 1, 3, 8, 9)) & np.isfinite(arr).all(axis=0)
        hz = noncloud & (b2 - 0.5 * b4 > HOT_MIN) & (_nd(b8, b4) > NDVI_HAZE_MIN)
        cir = noncloud & (scl == 10)
        tot["haze"][0] += int((hz & cir).sum()); tot["haze"][1] += int((hz & ~cir).sum()); tot["haze"][2] += int((~hz & cir).sum())
        burn_pix += int(m["burn_scar"].sum()); valid_pix += int(m["valid"].sum())
        lab_path = Path(labels_root) / sid / "labels.geojson" if labels_root else None
        if lab_path is not None and lab_path.exists():
            import geopandas as gpd
            from .labels import rasterize_to
            lab = rasterize_to(p / "reflectance.tif", gpd.read_file(lab_path)).astype(bool) & m["valid"]
            for k in on_mining:   # how often each prompt fires on mining ground itself, and elsewhere
                on_mining[k][0] += int((m[k] & lab).sum()); on_mining[k][1] += int(lab.sum())
                off_mining[k][0] += int((m[k] & m["valid"] & ~lab).sum()); off_mining[k][1] += int((m["valid"] & ~lab).sum())
        for f in _summarise(m)["flags"]:
            sites_flagged[f["condition"]] += 1
        used.append(sid)
    prf = lambda t: {"precision": t[0] / (t[0] + t[1]) if t[0] + t[1] else None, "recall": t[0] / (t[0] + t[2]) if t[0] + t[2] else None,
                     "tp": t[0], "fp": t[1], "fn": t[2]}
    res = {"sites": len(used), "water_vs_worldcover": prf(tot["water"]), "haze_vs_scl_cirrus": prf(tot["haze"]),
           "burn_scar": {"share_of_usable_pixels_flagged": burn_pix / valid_pix if valid_pix else None,
                         "evaluated": False, "note": "no labelled burn scars available; this is how often the rule fires, not accuracy"},
           "sites_with_tile_flag": sites_flagged,
           "share_flagged_on_labelled_mining": {k: (v[0] / v[1] if v[1] else None) for k, v in on_mining.items()},
           "share_flagged_elsewhere": {k: (v[0] / v[1] if v[1] else None) for k, v in off_mining.items()},
           "thresholds": {"HOT_MIN": HOT_MIN, "NDVI_HAZE_MIN": NDVI_HAZE_MIN, "BURN_NBR_MAX": BURN_NBR_MAX, "BURN_NIR_MAX": BURN_NIR_MAX,
                          "BURN_RED_MAX": BURN_RED_MAX, "WATER_NDWI_MIN": WATER_NDWI_MIN, "TILE_FLAG_AT": TILE_FLAG_AT},
           "site_ids": used}
    if out:
        Path(out).write_text(json.dumps(res, indent=1))
    return res
