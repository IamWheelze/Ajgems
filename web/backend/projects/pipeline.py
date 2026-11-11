"""Detection job steps (FR-020, FR-050 to FR-053, FR-060 to FR-062, FR-080 to FR-085).

Plain functions, so tests can replace the catalogue and the model with synthetic ones.
All raster work goes through the minefoot package, with the same preprocessing as training.
"""
from __future__ import annotations
import json
import math
from functools import lru_cache
from pathlib import Path

import numpy as np
from django.conf import settings


# ---------- area to tile grid ----------

def utm_epsg(lon, lat):
    return (32600 if lat >= 0 else 32700) + int(min(max((lon + 180) // 6 + 1, 1), 60))


def tile_grid(area_geom_4326, tile_km: float):
    """10 km (by default) square tiles in the area's UTM zone that intersect the area.

    Returns a list of (grid_id, shapely polygon in EPSG:4326)."""
    import geopandas as gpd
    from shapely import wkt
    from shapely.geometry import box
    g = wkt.loads(area_geom_4326.wkt)
    c = g.centroid
    epsg = utm_epsg(c.x, c.y)
    gu = gpd.GeoSeries([g], crs=4326).to_crs(epsg).iloc[0]
    step = tile_km * 1000
    minx, miny, maxx, maxy = gu.bounds
    x0, y0 = math.floor(minx / step) * step, math.floor(miny / step) * step
    tiles = []
    for i, y in enumerate(np.arange(y0, maxy, step)):
        for j, x in enumerate(np.arange(x0, maxx, step)):
            t = box(x, y, x + step, y + step)
            if t.intersects(gu):
                tiles.append((f"z{epsg % 100}_r{int(y // step)}_c{int(x // step)}", t))
    out = gpd.GeoSeries([t for _, t in tiles], crs=epsg).to_crs(4326)
    return [(gid, geom) for (gid, _), geom in zip(tiles, out)]


# ---------- catalogue ----------

def allowed_providers(use):
    """FR-025: catalogues in fallback order (FR-024) that are approved for this intended use, and refusals."""
    from minefoot.catalog import default_providers
    from .models import SourceApproval
    ok = {a.provider: a for a in SourceApproval.objects.filter(use=use)}
    allowed = [p for p in default_providers() if p in ok and ok[p].approved]
    refused = {p: ok[p].licence_evidence if p in ok else "no approval recorded" for p in default_providers() if p not in allowed}
    return allowed, refused


def find_scenes(bbox, start, end, max_cloud, providers=None):
    """Scenes for the job, clearest first, from the first catalogue that answers (FR-024). Returns (provider, [item dicts])."""
    from minefoot.catalog import search
    prov, items = search(bbox, start, end, max_cloud, providers=providers)
    return prov, [it.to_dict() for it in items]


def choose_scene(items, tile_geom):
    """Clearest scene that fully covers the tile, else the one covering most of it (FR-061)."""
    from shapely.geometry import shape
    best = None
    for it in items:
        cover = shape(it["geometry"]).intersection(tile_geom).area / tile_geom.area
        key = (cover < 0.999, -round(cover, 3), it["properties"].get("eo:cloud_cover", 100), it["id"])
        if best is None or key < best[0]:
            best = (key, it, cover)
    return (best[1], best[2]) if best else (None, 0.0)


def fetch_tile(provider, item_dict, tile_bbox, out_dir, name):
    """Windowed fetch and the one preprocessing path. Returns the prepared folder."""
    import pystac
    from minefoot.catalog import PROVIDERS
    from minefoot.catalog import fetch_scene
    from minefoot.preprocess import prepare_scene
    item = pystac.Item.from_dict(item_dict)
    if PROVIDERS[provider].get("sign"):
        import planetary_computer
        item = planetary_computer.sign(item)
    raw = fetch_scene(item, provider, tile_bbox, Path(out_dir) / "raw", name=name)
    prep = Path(out_dir) / "prepared" / name
    prepare_scene(raw, prep)
    (raw / "raw_dn.tif").unlink(missing_ok=True)
    return prep


# ---------- model ----------

@lru_cache(maxsize=2)
def _model(run_dir):
    from minefoot.g2 import load_run_model
    return load_run_model(run_dir)


def active_model():
    """(run folder, name) of the model jobs use: the registry's production model, else MINEFOOT_MODEL_RUN."""
    from registry.services import production
    mv = production()
    if mv is not None:
        return Path(mv.run_path), mv.name
    run = Path(settings.MINEFOOT["MODEL_RUN"])
    return run, f"{run.name}:unet (unregistered)"


def check_schema(run_dir):
    """FR-102: inference refuses a model whose input schema does not match."""
    from minefoot.schema import SCHEMA_VERSION
    cfg = json.loads((Path(run_dir) / "config.json").read_text())
    if cfg.get("input_schema") != SCHEMA_VERSION:
        raise ValueError(f"model input schema {cfg.get('input_schema')!r} does not match inference {SCHEMA_VERSION!r}")


def model_card(run_dir=None) -> dict:
    """What the run setup screen shows about the model (FR-014 domain warning, FR-084 wording)."""
    active, name = active_model()
    run_dir = Path(run_dir or active)
    m = json.loads((run_dir / "metrics.json").read_text())["models"]["unet"]
    cfg = json.loads((run_dir / "config.json").read_text())
    test = m["test"]["strata"]["all"]
    tested_in_nigeria = Path(settings.MINEFOOT["NIGERIA_G3_RESULT"]).exists()
    check_schema(run_dir)
    return {"model_version": name if run_dir == active else f"{run_dir.name}:unet", "model_run": str(run_dir),
            "threshold": m["threshold_score"], "score_type": "model score",
            "trained_on": "Global ML-ready (internal research)", "code_commit": cfg.get("code_commit"),
            "global_test_f1": test["f1"], "global_test_f1_ci95": test["ci95"]["f1"],
            "tested_in_nigeria": tested_in_nigeria,
            "domain_warning": None if tested_in_nigeria else
            "Not yet tested on Nigerian sites (gate G3 pending). Treat every candidate as unverified."}


def score_prepared(prep_dir, run_dir=None):
    """Model score in [0, 1] for a prepared tile, 0 where unusable."""
    import rasterio
    from minefoot.g2 import predict_tile
    from minefoot.schema import BANDS
    model, norm, level, cfg, dev = _model(str(run_dir or active_model()[0]))
    with rasterio.open(Path(prep_dir) / "reflectance.tif") as s:
        refl = s.read()
    return predict_tile(model, refl[:len(BANDS)], norm, dev, cfg["chip"], cfg.get("infer_overlap") or cfg["chip"] // 4)


def write_score(score, prep_dir, path):
    import rasterio
    from minefoot.baseline import to_levels
    with rasterio.open(Path(prep_dir) / "reflectance.tif") as s:
        p = {**s.profile, "count": 1, "dtype": "uint8", "nodata": None, "compress": "deflate"}
    with rasterio.open(path, "w", **p) as d:
        d.write(to_levels(score), 1)
    return path


# ---------- embargo on the Nigerian test regions (ML-07) ----------

def embargoed(area_geom_4326) -> str | None:
    """Refuse jobs over the labelling-kit regions until the Nigerian test labels are locked."""
    import yaml
    from shapely import wkt
    from shapely.geometry import box
    cfg_path = Path(settings.MINEFOOT["NIGERIA_AOIS"])
    lock = Path(settings.MINEFOOT["NIGERIA_TEST_LOCK"])
    if lock.exists() or not cfg_path.exists():
        return None
    g = wkt.loads(area_geom_4326.wkt)
    for r in yaml.safe_load(cfg_path.read_text()).get("regions", []):
        if g.intersects(box(*r["bbox"])):
            return (f"this area overlaps the Nigerian test region '{r['name']}'. Jobs there are refused until the "
                    f"Nigerian test labels are locked, so model output cannot influence labelling (ML-07).")
    return None


# ---------- validated domain (FR-014, FR-083) ----------

DOMAIN_MAX_KM = 500        # provisional: an area this far from every test site is outside the validated domain
SEASON_RADIUS_KM = 1000    # look for test scenes from the same months within this distance


def domain_check(area_geom_4326, start=None, end=None, run_dir=None) -> dict:
    """Where the active model was tested, compared with this area and date window. Measured facts only:
    distance to the nearest locked test site of the model's run, and whether any nearby test scene was taken
    in the same months. Warnings are provisional rules (DOMAIN_MAX_KM, SEASON_RADIUS_KM), not calibrated."""
    from datetime import date
    from django.contrib.gis.db.models.functions import Distance
    from datasets.models import Sample
    run = Path(run_dir or active_model()[0])
    try:
        sites = json.loads((run / "config.json").read_text())["sites"]["test"]
    except (OSError, KeyError, ValueError):
        sites = None
    qs = Sample.objects.filter(split="test")
    if sites:
        qs = qs.filter(site_id__in=sites)
    c = area_geom_4326.centroid
    rows = list(qs.annotate(d=Distance("footprint", c)).order_by("d").values("site_id", "region", "acquired", "d")[:400])
    out = {"model_run": run.name, "test_sites": qs.count(), "flags": [], "inside": None,
           "rule": f"inside if a locked test site of the model is within {DOMAIN_MAX_KM} km and a test scene within "
                   f"{SEASON_RADIUS_KM} km was taken in the same months (provisional)"}
    if not rows:
        out["flags"].append("No test sites are recorded for this model, so its domain is unknown. Import the dataset version.")
        return out
    near = rows[0]
    out.update({"nearest_test_site": near["site_id"], "nearest_test_km": round(near["d"].km), "nearest_region": near["region"]})
    if near["d"].km > DOMAIN_MAX_KM:
        out["flags"].append(f"Outside the validated domain: the nearest test site of this model is {near['d'].km:,.0f} km away "
                            f"({near['region']}). Results here are unverified.")
    if start and end:
        d0, d1 = date.fromisoformat(start), date.fromisoformat(end)
        months = {(d0.month + i - 1) % 12 + 1 for i in range(min(12, (d1.year - d0.year) * 12 + d1.month - d0.month + 1))}
        same = [r for r in rows if r["d"].km <= SEASON_RADIUS_KM and r["acquired"] and r["acquired"].month in months]
        out["same_season_test_sites"] = len(same)
        if not same:
            out["flags"].append(f"Season not tested nearby: no test scene within {SEASON_RADIUS_KM:,} km was taken in "
                                f"{', '.join(date(2000, m, 1).strftime('%b') for m in sorted(months))}.")
    out["inside"] = not out["flags"]
    return out
