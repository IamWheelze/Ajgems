"""Hard negatives tagged by type (FR-074, ML-08).

Two sources:
1. Digitised by people, following docs/labelling_protocol.md: a GeoPackage made by
   `write_template`, checked by `check_negatives`. This is the real source.
2. A proxy from ESA WorldCover 2021 v200 (CC BY 4.0), for datasets with no typed
   negatives. WorldCover has no mining class and mining labels are incomplete,
   so proxy negatives are taken only beyond `buffer_px` of any mining label.
   The proxy covers farmland, settlement, riverbank and bare ground only, and
   WorldCover's date (2021) can differ from the scene date.
"""
from __future__ import annotations
import json
from pathlib import Path

import numpy as np

PROTOCOL_TYPES = ("quarry", "borrow_pit", "farmland", "riverbank", "burn_scar", "settlement", "dry_pond",
                  "construction", "bare_ground")
TEMPLATE_FIELDS = {"neg_type": "str", "protocol_version": "str", "scene_id": "str", "image_date": "str",
                   "annotator": "str", "evidence_grade": "str", "notes": "str"}
EVIDENCE_GRADES = ("E0", "E1", "E2", "E3")
MIN_AREA_HA = 0.5

# WorldCover v200 class codes.
WC = {"tree": 10, "shrub": 20, "grass": 30, "crop": 40, "built": 50, "bare": 60, "snow": 70, "water": 80,
      "wetland": 90, "mangrove": 95, "moss": 100}
# Proxy type codes in the typed negative map.
PROXY_TYPES = {1: "farmland", 2: "settlement", 3: "riverbank", 4: "bare_ground", 5: "other_land"}
WORLDCOVER = {"collection": "esa-worldcover", "version": "v200 (2021)", "licence": "CC BY 4.0",
              "credit": "© ESA WorldCover project 2021 / Contains modified Copernicus Sentinel data (2021) "
                        "processed by ESA WorldCover consortium"}


# ---------- digitised negatives ----------

def write_empty_polygon_layer(path, layer, fields):
    """Empty GeoPackage layer with an explicit Polygon type and EPSG:4326, so QGIS can digitise into it.

    An empty GeoDataFrame written the usual way has no geometry type or CRS recorded.
    """
    import pyogrio
    import geopandas as gpd
    import pandas as pd
    df = gpd.GeoDataFrame({f: pd.Series(dtype="object") for f in fields}, geometry=gpd.GeoSeries([], crs=4326), crs=4326)
    pyogrio.write_dataframe(df, path, layer=layer, driver="GPKG", geometry_type="Polygon")

def write_template(path, protocol_version="0.1") -> Path:
    """Empty polygon GeoPackage with the protocol's fields, plus a QGIS style whose neg_type is a drop-down."""
    import geopandas as gpd
    import pandas as pd
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    cols = {k: pd.Series(dtype="object") for k in TEMPLATE_FIELDS}
    write_empty_polygon_layer(path, "hard_negatives", list(TEMPLATE_FIELDS))
    opts = "".join(f'<Option type="Map"><Option name="{t}" type="QString" value="{t}"/></Option>' for t in PROTOCOL_TYPES)
    grades = "".join(f'<Option type="Map"><Option name="{g}" type="QString" value="{g}"/></Option>' for g in EVIDENCE_GRADES)
    qml = ("<!DOCTYPE qgis PUBLIC 'http://mrcc.com/qgis.dtd' 'SYSTEM'>\n<qgis version=\"3.34.0\"><fieldConfiguration>"
           '<field name="neg_type"><editWidget type="ValueMap"><config><Option type="Map">'
           f'<Option name="map" type="List">{opts}</Option></Option></config></editWidget></field>'
           '<field name="evidence_grade"><editWidget type="ValueMap"><config><Option type="Map">'
           f'<Option name="map" type="List">{grades}</Option></Option></config></editWidget></field>'
           "</fieldConfiguration><defaults>"
           f'<default field="protocol_version" expression="\'{protocol_version}\'" applyOnUpdate="0"/>'
           '<default field="annotator" expression="@user_full_name" applyOnUpdate="0"/>'
           "</defaults></qgis>\n")
    path.with_suffix(".qml").write_text(qml)
    return path


def check_negatives(path, layer="hard_negatives") -> dict:
    """Counts per type and named problems for a digitised hard-negative layer (FR-074)."""
    import geopandas as gpd
    g = gpd.read_file(path, layer=layer)
    problems = [f"missing field {f}" for f in TEMPLATE_FIELDS if f not in g.columns]
    if problems:
        return {"features": int(len(g)), "problems": problems}
    if g.crs is None:
        problems.append("layer has no CRS")
    bad_type = g.loc[~g["neg_type"].isin(PROTOCOL_TYPES), "neg_type"].unique().tolist()
    if bad_type:
        problems.append(f"unknown neg_type values: {bad_type}")
    for f in ("protocol_version", "scene_id", "image_date", "annotator"):
        n = int(g[f].isna().sum() + (g[f].astype(str).str.strip() == "").sum())
        if n:
            problems.append(f"{n} feature(s) without {f}")
    bad_grade = int((~g["evidence_grade"].isin(EVIDENCE_GRADES)).sum())
    if bad_grade:
        problems.append(f"{bad_grade} feature(s) without a valid evidence_grade (E0 to E3)")
    invalid = int((~g.geometry.is_valid).sum()) if len(g) else 0
    if invalid:
        problems.append(f"{invalid} invalid geometr{'y' if invalid == 1 else 'ies'}")
    small = 0
    if len(g) and g.crs is not None:
        c = g.geometry.representative_point().to_crs(4326)
        from .splits import _utm_epsg
        epsg = _utm_epsg(float(c.x.median()), float(c.y.median()))
        small = int((g.to_crs(epsg).area < MIN_AREA_HA * 1e4).sum())
        if small:
            problems.append(f"{small} polygon(s) smaller than {MIN_AREA_HA} ha")
    return {"features": int(len(g)), "by_type": {t: int((g["neg_type"] == t).sum()) for t in PROTOCOL_TYPES},
            "problems": problems}


# ---------- WorldCover proxy ----------

def fetch_worldcover(prepared_dir, out_path) -> Path:
    """WorldCover classes on the site's 10 m grid (nearest neighbour), cached as a GeoTIFF."""
    import rasterio
    from rasterio.warp import Resampling, reproject, transform_bounds
    from .catalog import _client
    out_path = Path(out_path)
    if out_path.exists():
        return out_path
    with rasterio.open(Path(prepared_dir) / "scl.tif") as ref:
        prof, crs, tr, shape = ref.profile, ref.crs, ref.transform, (ref.height, ref.width)
        bounds = transform_bounds(crs, "EPSG:4326", *ref.bounds, densify_pts=21)
    items = list(_client("planetary").search(collections=[WORLDCOVER["collection"]], bbox=list(bounds)).items())
    if not items:
        raise LookupError(f"no WorldCover tile for {bounds}")
    dst = np.zeros(shape, "uint8")
    for it in items:
        with rasterio.open(it.assets["map"].href) as src:
            win = rasterio.windows.from_bounds(*bounds, transform=src.transform)
            win = win.round_offsets().round_lengths() if hasattr(win, "round_offsets") else win
            data = src.read(1, window=win, boundless=True, fill_value=0)
            part = np.zeros(shape, "uint8")
            reproject(data, part, src_transform=src.window_transform(win), src_crs=src.crs, dst_transform=tr,
                      dst_crs=crs, resampling=Resampling.nearest, src_nodata=0, dst_nodata=0)
            dst = np.where(dst == 0, part, dst)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    p = {**prof, "count": 1, "dtype": "uint8", "nodata": 0, "compress": "deflate"}
    with rasterio.open(out_path, "w", **p) as d:
        d.write(dst, 1)
        d.update_tags(source=f"ESA WorldCover {WORLDCOVER['version']}", licence=WORLDCOVER["licence"],
                      credit=WORLDCOVER["credit"], tiles=",".join(i.id for i in items))
    return out_path


def proxy_type_map(wc: np.ndarray, label: np.ndarray, buffer_px: int = 50, bank_px: int = 3) -> np.ndarray:
    """Typed negative map from WorldCover: 0 none (or too near a label), else a PROXY_TYPES code.

    Pixels within `buffer_px` of a mining label are excluded, because labels miss
    some mining next to labelled mines. Riverbank is non-water land within
    `bank_px` of permanent water.
    """
    from scipy import ndimage
    near = ndimage.binary_dilation(label, iterations=buffer_px) if label.any() else np.zeros_like(label)
    water = wc == WC["water"]
    bank = ndimage.binary_dilation(water, iterations=bank_px) & ~water & (wc != 0)
    t = np.zeros(wc.shape, "uint8")
    t[np.isin(wc, (WC["tree"], WC["shrub"], WC["grass"], WC["wetland"], WC["mangrove"], WC["moss"]))] = 5
    t[wc == WC["bare"]] = 4
    t[wc == WC["built"]] = 2
    t[wc == WC["crop"]] = 1
    t[bank] = 3          # riverbank overrides the land class under it
    t[near | water | (wc == 0) | (wc == WC["snow"])] = 0
    return t


def false_alerts_by_type(pred: np.ndarray, valid: np.ndarray, types: np.ndarray) -> dict:
    """Per proxy type: usable pixels of that type and how many were predicted as disturbance."""
    out = {}
    for code, name in PROXY_TYPES.items():
        m = (types == code) & valid
        out[name] = {"pixels": int(m.sum()), "predicted": int((pred & m).sum())}
    return out


def pooled_rates(rows: list[dict], n_boot=1000, seed=0) -> dict:
    """False-alert rate per type over sites, with a site-bootstrap 95% interval."""
    rng = np.random.default_rng(seed)
    out = {}
    for name in PROXY_TYPES.values():
        px = np.array([r[name]["pixels"] for r in rows], float)
        pr = np.array([r[name]["predicted"] for r in rows], float)
        if px.sum() == 0:
            out[name] = {"pixels": 0, "rate": None, "ci95": None, "sites_with_type": 0}
            continue
        bs = []
        for _ in range(n_boot):
            i = rng.integers(0, len(rows), len(rows))
            bs.append(pr[i].sum() / px[i].sum() if px[i].sum() else np.nan)
        out[name] = {"pixels": int(px.sum()), "rate": float(pr.sum() / px.sum()),
                     "ci95": [float(np.nanpercentile(bs, 2.5)), float(np.nanpercentile(bs, 97.5))],
                     "sites_with_type": int((px > 0).sum())}
    return out


def evaluate_run_by_type(run_dir, model: str, split: str, prepared_root, labels_root, worldcover_root,
                         buffer_px: int = 50) -> dict:
    """Per-type false-alert rates for a stored G1-style run, at the threshold it chose on validation."""
    import rasterio
    from .baseline import load_site
    run_dir = Path(run_dir)
    m = json.loads((run_dir / "metrics.json").read_text())["models"][model]
    t = m["threshold_level"]
    rows = []
    for r in m[split]["per_site"]:
        sid = r["site_id"]
        _, valid, label, _ = load_site(Path(prepared_root) / sid, Path(labels_root) / sid / "labels.geojson")
        wc_path = fetch_worldcover(Path(prepared_root) / sid, Path(worldcover_root) / f"{sid}.tif")
        with rasterio.open(wc_path) as s:
            wc = s.read(1)
        with rasterio.open(run_dir / "scores" / f"{sid}__{model}.tif") as s:
            pred = (s.read(1) >= t) & valid
        rows.append({"site_id": sid, **false_alerts_by_type(pred, valid, proxy_type_map(wc, label, buffer_px))})
    return {"model": model, "split": split, "threshold_level": t, "buffer_px": buffer_px,
            "source": f"ESA WorldCover {WORLDCOVER['version']} proxy types", "by_type": pooled_rates(rows),
            "per_site": rows}


def read_vectors_layer(path, layer="hard_negatives"):
    import geopandas as gpd
    return gpd.read_file(path, layer=layer)
