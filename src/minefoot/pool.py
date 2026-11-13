"""Adapter for labels accepted through the web app's annotation pool (FR-070, FR-075).

Input: the zip from `Download accepted labels` (labels.geojson + manifest.json). Only examples that passed
independent QC are in it. Output: the common schema (`sites.gpkg` with layers sites, labels, hard_negatives, plus
`ingest_report.json`), like every other adapter, so the split, fetch and training steps treat it the same way.

Sites: examples on the same scene within `join_m` of each other form one site, whose footprint is their bounds
plus `pad_m`, so training chips see the surroundings. Nothing that overlaps a locked test site or a Nigerian
test region is accepted (FR-077, ML-07); every dropped example is counted with its reason.
"""
from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path

from .datasets import _region, check_sites, write_ingest

POOL = {"name": "annotation_pool", "licence": "Own reviews, independently QC'd; on Copernicus Sentinel-2 data",
        "source": "minefoot web app annotation pool (FR-075)"}
# Pool reject reasons map onto the digitising protocol's hard-negative types (negatives.PROTOCOL_TYPES).
NEG_TYPE = {"farmland": "farmland", "settlement": "settlement", "riverbank": "riverbank", "bare_ground": "bare_ground",
            "quarry_or_borrow_pit": "quarry", "burn_scar": "burn_scar"}


def _utm(lon, lat):
    return (32600 if lat >= 0 else 32700) + int((lon + 180) // 6) + 1


def load_pool_export(zip_path, locked_sites_gpkg=None, test_regions_yaml=None, join_m=1000, pad_m=500, version=None):
    import geopandas as gpd
    import pandas as pd
    from shapely.geometry import box
    data = Path(zip_path).read_bytes()
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        fc = json.loads(z.read("labels.geojson")); manifest = json.loads(z.read("manifest.json"))
    if hashlib.sha256(z_bytes(zip_path, "labels.geojson")).hexdigest() != manifest.get("sha256"):
        raise ValueError("labels.geojson does not match the manifest's SHA-256: the export was altered")
    g = gpd.GeoDataFrame.from_features(fc["features"], crs=4326)
    dropped = {}
    def drop(mask, why):
        n = int(mask.sum())
        if n:
            dropped[why] = dropped.get(why, 0) + n
        return g[~mask]
    if len(g):
        g = drop(~g["independent_qc"].fillna(False).astype(bool), "not independently QC'd")
        g = drop(g["scene_id"].fillna("").astype(str) == "", "no scene_id")
    if len(g) and test_regions_yaml and Path(test_regions_yaml).exists():
        import yaml
        regions = [box(*r["bbox"]) for r in yaml.safe_load(Path(test_regions_yaml).read_text()).get("regions", [])]
        g = drop(g.geometry.apply(lambda x: any(x.intersects(r) for r in regions)), "inside a Nigerian test region (ML-07)")
    if len(g) and locked_sites_gpkg:
        locked = gpd.read_file(locked_sites_gpkg, layer="sites")
        locked = locked[locked["split"] == "test"].to_crs(4326) if "split" in locked.columns else locked.to_crs(4326)
        hit = g.geometry.apply(lambda x: bool(locked.sindex.query(x, predicate="intersects").size))
        g = drop(hit, "overlaps a locked test site (FR-077)")
    g = g.copy()
    neg = g[g["label_class"] == "hard_negative"].copy()
    unmapped = neg["negative_type"].map(NEG_TYPE).isna()
    if unmapped.any():
        dropped["hard negative type not in the protocol"] = int(unmapped.sum())
        g = g.drop(neg[unmapped].index)
    g["neg_type"] = g["negative_type"].map(NEG_TYPE)
    # Join examples on the same scene within join_m into sites.
    rows, site_of = [], {}
    for scene, part in g.groupby("scene_id"):
        c = part.geometry.union_all().centroid
        utm = _utm(c.x, c.y); pu = part.to_crs(utm)
        merged = gpd.GeoSeries(list(pu.geometry.buffer(join_m / 2).union_all().geoms)
                               if pu.geometry.buffer(join_m / 2).union_all().geom_type == "MultiPolygon"
                               else [pu.geometry.buffer(join_m / 2).union_all()], crs=utm)
        for k, cluster in enumerate(merged):
            members = pu[pu.intersects(cluster)]
            fp = gpd.GeoSeries([box(*members.total_bounds).buffer(pad_m, join_style=2)], crs=utm).to_crs(4326).iloc[0]
            sid = "pool-" + hashlib.sha256(f"{scene}|{','.join(sorted(members['example_id']))}".encode()).hexdigest()[:10]
            for i in members.index:
                site_of[i] = sid
            acquired = str(members["scene_date"].iloc[0])[:10]
            rows.append({"dataset": POOL["name"], "dataset_version": version or manifest["created_at"][:10], "licence": POOL["licence"],
                         "source": POOL["source"], "site_id": sid, "scene_id": scene, "acquired": acquired, "mgrs_tile": "",
                         "source_split": "", "mine_type": "unknown", "mine_scale": "unknown", "label_source": "annotation_pool",
                         "examples": len(members), "mining_examples": int((members["label_class"] == "mining").sum()),
                         "geometry": fp})
    sites = gpd.GeoDataFrame(rows, crs=4326) if rows else gpd.GeoDataFrame(columns=["site_id", "geometry"], geometry="geometry", crs=4326)
    g["site_id"] = g.index.map(site_of)
    keep = ["site_id", "example_id", "protocol_version", "qc_reason", "qc_at", "scene_id", "scene_date", "geometry"]
    labels = g[g["label_class"] == "mining"][keep].assign(label_source="annotation_pool")
    negs = g[g["label_class"] == "hard_negative"][keep + ["neg_type"]].assign(
        annotator="annotation pool", evidence_grade="E1", image_date=lambda d: d["scene_date"].astype(str).str[:10],
        notes="independently QC'd review")
    if len(sites):
        sites["region"] = [_region(p.centroid.x, p.centroid.y) for p in sites.geometry]
    report = {"dataset": POOL["name"], "version": version or manifest["created_at"][:10], "licence": POOL["licence"],
              "export_sha256": manifest.get("sha256"), "examples_in": manifest.get("examples"), "dropped": dropped,
              "sites_out": int(len(sites)), "mining_examples_out": int(len(labels)), "hard_negatives_out": int(len(negs)),
              "hard_negatives_by_type": {k: int(v) for k, v in negs["neg_type"].value_counts().items()},
              "schema_problems": check_sites(sites) if len(sites) else []}
    return sites, gpd.GeoDataFrame(labels, crs=4326), gpd.GeoDataFrame(negs, crs=4326), report


def z_bytes(zip_path, name):
    with zipfile.ZipFile(zip_path) as z:
        return z.read(name)


def write_pool(sites, labels, negs, report, out_dir) -> Path:
    path = write_ingest(sites, labels, report, out_dir)
    if len(negs):
        negs.to_file(path, layer="hard_negatives", driver="GPKG")
    return path
