"""Dataset adapters into one common site schema (FR-070, FR-071).

Each adapter returns sites (one row per labelled tile, with its exact scene),
labels (polygons keyed by site_id) and a report that counts every dropped row
with its reason. Nothing is dropped silently.

The source dataset's own train/val/test split is kept only as `source_split`
for reference. Project splits are made by site and geographic block (FR-077).
"""
from __future__ import annotations
import json
import re
from pathlib import Path

SITE_COLUMNS = ("dataset", "dataset_version", "licence", "source", "site_id", "scene_id", "acquired",
                "mgrs_tile", "source_split", "mine_type", "mine_scale", "label_source")
LABEL_COLUMNS = ("site_id", "label_source")

# Sentinel-2 product IDs as served by Planetary Computer, e.g.
# S2B_MSIL2A_20191004T074749_R135_T35KQR_20201003T223418
_S2_ID = re.compile(r"^S2[ABC]_MSIL2A_(\d{8})T\d{6}_R\d{3}_T(\d{2}[A-Z]{3})_\d{8}T\d{6}$")

GLOBAL_ML_READY = {
    "name": "global_ml_ready",
    "version": "1.0.0",
    "licence": "CC BY-SA 4.0",
    "source": "https://doi.org/10.5281/zenodo.14195737",
    "provider": "planetary",  # the only catalogue found to serve these exact product IDs
}


def parse_s2_id(scene_id: str) -> tuple[str, str]:
    """Return (acquisition date YYYY-MM-DD, MGRS tile) from a Planetary Computer S2 L2A ID."""
    m = _S2_ID.match(scene_id or "")
    if not m:
        raise ValueError(f"not a Sentinel-2 L2A product ID: {scene_id!r}")
    d = m.group(1)
    return f"{d[:4]}-{d[4:6]}-{d[6:]}", m.group(2)


def check_sites(sites) -> list[str]:
    """Schema problems in a sites table. An empty list means it passes."""
    problems = [f"missing column {c}" for c in SITE_COLUMNS if c not in sites.columns]
    if problems:
        return problems
    if sites["site_id"].duplicated().any():
        problems.append("duplicate site_id")
    for c in ("site_id", "scene_id", "licence", "dataset_version"):
        if sites[c].isna().any() or (sites[c].astype(str) == "").any():
            problems.append(f"empty {c}")
    if sites.crs is None or sites.crs.to_epsg() != 4326:
        problems.append("sites must be in EPSG:4326")
    if sites.geometry.isna().any() or sites.geometry.is_empty.any():
        problems.append("site without footprint")
    return problems


def load_global_ml_ready(gpkg, label_layer: str = "preferred_polygons"):
    """Read the Global ML-ready GeoPackage (Zenodo 14195737 v1.0.0).

    Layer `tiles` holds tile_id, s2_tile_id, split and mine type; label layers hold
    tile_id and geometry only (checked against the released file on 2026-11-03).
    A tile is kept only if it has a source split, a parseable scene ID and a
    non-empty label. Tiles the authors rejected have no split and an empty label.
    """
    import geopandas as gpd
    meta = GLOBAL_ML_READY
    tiles = gpd.read_file(gpkg, layer="tiles")
    labels = gpd.read_file(gpkg, layer=label_layer)
    report = {"dataset": meta["name"], "version": meta["version"], "licence": meta["licence"],
              "source": meta["source"], "file": str(gpkg), "label_layer": label_layer,
              "tiles_in": int(len(tiles)), "dropped": {}}

    def drop(mask, reason):
        nonlocal tiles
        n = int(mask.sum())
        if n:
            report["dropped"][reason] = report["dropped"].get(reason, 0) + n
        tiles = tiles[~mask]

    drop(tiles["source_dataset"].eq("rejected"), "rejected_by_dataset_authors")
    drop(tiles["split"].isna(), "no_source_split")
    ok_id = tiles["s2_tile_id"].map(lambda s: bool(_S2_ID.match(s or "")))
    drop(~ok_id, "unparseable_scene_id")
    labels = labels[labels.geometry.notna() & ~labels.geometry.is_empty]
    drop(~tiles["tile_id"].isin(labels["tile_id"]), "no_label_geometry")
    labels = labels[labels["tile_id"].isin(tiles["tile_id"])]

    parsed = tiles["s2_tile_id"].map(parse_s2_id)
    sites = gpd.GeoDataFrame({
        "dataset": meta["name"], "dataset_version": meta["version"], "licence": meta["licence"],
        "source": meta["source"],
        "site_id": tiles["tile_id"].map(lambda i: f"gmlr-{int(i)}"),
        "scene_id": tiles["s2_tile_id"],
        "acquired": parsed.map(lambda p: p[0]),
        "mgrs_tile": parsed.map(lambda p: p[1]),
        "source_split": tiles["split"],
        "mine_type": tiles["minetype1"],
        "mine_scale": tiles["minetype2"],
        "label_source": tiles["preferred_dataset"],
    }, geometry=tiles.geometry.values, crs=tiles.crs).to_crs(4326).reset_index(drop=True)
    lab = gpd.GeoDataFrame({"site_id": labels["tile_id"].map(lambda i: f"gmlr-{int(i)}"),
                            "label_source": label_layer},
                           geometry=labels.geometry.values, crs=labels.crs).to_crs(4326).reset_index(drop=True)
    report["sites_out"] = int(len(sites))
    report["label_features_out"] = int(len(lab))
    report["by_source_split"] = {k: int(v) for k, v in sites["source_split"].value_counts().items()}
    report["by_mine_scale"] = {k: int(v) for k, v in sites["mine_scale"].fillna("unknown").value_counts().items()}
    report["schema_problems"] = check_sites(sites)
    return sites, lab, report


def write_ingest(sites, labels, report, out_dir) -> Path:
    """Write sites.gpkg (layers sites, labels) and ingest_report.json."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / "sites.gpkg"
    if path.exists():
        path.unlink()
    sites.to_file(path, layer="sites", driver="GPKG")
    labels.to_file(path, layer="labels", driver="GPKG")
    (out / "ingest_report.json").write_text(json.dumps(report, indent=2))
    return path


def _region(lon: float, lat: float) -> str:
    """Coarse region for stratified sampling only. Not used for splits."""
    if -18 < lon < 52 and -35 < lat < 37:
        return "africa"
    if lon < -30:
        return "americas"
    if lon > 60 and lat < 55:
        return "asia_oceania"
    return "europe_north_asia"


def select_sites(sites, n: int, seed: int = 0):
    """Deterministic sample stratified by coarse region and mine scale, Africa weighted up.

    Half the sample (or all African sites, if fewer) comes from Africa, because the
    Nigerian work cares most about how African sites look. The rest is spread over
    the other regions.
    """
    import geopandas as gpd
    import pandas as pd
    if n >= len(sites):
        return sites.copy()
    c = sites.to_crs(3857).geometry.centroid.to_crs(4326)
    s = sites.assign(_region=[_region(x, y) for x, y in zip(c.x, c.y)])
    s = s.assign(_stratum=s["_region"] + "/" + s["mine_scale"].fillna("unknown"))
    af = s[s["_region"] == "africa"]
    k_af = min(len(af), n // 2)
    picks = [_stratified(af, k_af, seed), _stratified(s[s["_region"] != "africa"], n - k_af, seed)]
    out = pd.concat(picks).drop(columns=["_region", "_stratum"]).sort_values("site_id").reset_index(drop=True)
    return gpd.GeoDataFrame(out, geometry="geometry", crs=sites.crs)


def _stratified(df, k: int, seed: int):
    """Round-robin over strata so small strata are represented."""
    if k <= 0 or df.empty:
        return df.iloc[:0]
    groups = [g.sample(frac=1, random_state=seed) for _, g in sorted(df.groupby("_stratum"), key=lambda t: t[0])]
    idx, i = [], 0
    while len(idx) < k:
        took = False
        for g in groups:
            if i < len(g) and len(idx) < k:
                idx.append(g.index[i]); took = True
        if not took:
            break
        i += 1
    return df.loc[idx]


def fetch_sites(sites_gpkg, out_dir, n: int | None = None, site_ids=None, seed: int = 0, provider=None):
    """Fetch each site's exact scene, clipped to the site footprint, plus its labels.

    Writes <out>/<site_id>/ with raw_dn.tif and scene.json (from catalog.fetch_scene),
    labels.geojson and site.json. Existing folders are skipped, so the run can resume.
    Failures are recorded in fetch_report.json, never dropped silently.
    """
    import geopandas as gpd
    from .catalog import fetch_scene, get_item
    sites = gpd.read_file(sites_gpkg, layer="sites")
    labels = gpd.read_file(sites_gpkg, layer="labels")
    if site_ids:
        missing = sorted(set(site_ids) - set(sites["site_id"]))
        if missing:
            raise ValueError(f"unknown site_id: {missing}")
        chosen = sites[sites["site_id"].isin(site_ids)]
    else:
        chosen = select_sites(sites, n or len(sites), seed)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    report = {"requested": int(len(chosen)), "fetched": [], "skipped_existing": [], "failed": {}}
    for _, row in chosen.iterrows():
        sid = row["site_id"]
        d = out / sid
        if (d / "site.json").exists():
            report["skipped_existing"].append(sid)
            continue
        prov = provider or (GLOBAL_ML_READY["provider"] if row["dataset"] == GLOBAL_ML_READY["name"] else None)
        try:
            item = get_item(prov, row["scene_id"])
            fetch_scene(item, prov, list(row.geometry.bounds), out, name=sid)
            labels[labels["site_id"] == sid].to_file(d / "labels.geojson", driver="GeoJSON")
            info = {k: (None if v != v else v) for k, v in row.drop(labels="geometry").to_dict().items()}
            info["provider"] = prov
            (d / "site.json").write_text(json.dumps(info, indent=2, default=str))
            report["fetched"].append(sid)
            print(f"fetched {sid} {row['scene_id']}", flush=True)
        except Exception as exc:
            report["failed"][sid] = f"{type(exc).__name__}: {exc}"
            print(f"FAILED {sid}: {type(exc).__name__}: {exc}", flush=True)
    (out / "fetch_report.json").write_text(json.dumps(report, indent=2))
    return report
