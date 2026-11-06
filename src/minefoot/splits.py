"""Overlap detection (FR-072, ML-05) and site/block splits with a buffer (FR-077, ML-03).

Sites come from datasets.py's common schema: one row per site, geometry is the
site footprint in EPSG:4326, `scene_id` is the exact scene. Labels are optional
(site_id + geometry).

Splits are made from units. A unit is every site joined by any of: overlapping
footprints or labels, the same geographic block, or lying within `buffer_km` of
each other. Each unit goes wholly to one split, so no site or buffered block can
appear in two splits by construction. `check_split` verifies this independently.

A locked test set (`lock_test`) is a list of site IDs with a hash. Locked sites
always stay in test. New sites that would share a unit with a locked site are
held out, so the lock never changes.

Distances use each site's local UTM zone. Sites across the antimeridian are not
handled specially.
"""
from __future__ import annotations
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

SPLITS = ("train", "val", "test")
HELD_OUT = "held_out_near_locked_test"


# ---------- geometry helpers ----------

def _utm_epsg(lon: float, lat: float) -> int:
    zone = int(min(max((lon + 180) // 6 + 1, 1), 60))
    return (32600 if lat >= 0 else 32700) + zone


def _buffered_4326(gdf, metres: float):
    """Each geometry buffered by `metres` in its own UTM zone, returned in EPSG:4326."""
    import geopandas as gpd
    c = gdf.geometry.representative_point()
    epsg = [_utm_epsg(x, y) for x, y in zip(c.x, c.y)]
    out = gpd.GeoSeries([None] * len(gdf), index=gdf.index, crs=4326)
    for code in sorted(set(epsg)):
        idx = gdf.index[[e == code for e in epsg]]
        out.loc[idx] = gdf.loc[idx].geometry.to_crs(code).buffer(metres).to_crs(4326).values
    return out


def block_id(lon: float, lat: float, block_km: float) -> str:
    """Roughly square blocks of `block_km`: rows by latitude, columns widened by 1/cos(latitude)."""
    dlat = block_km / 111.32
    row = math.floor((lat + 90) / dlat)
    centre = -90 + (row + 0.5) * dlat
    dlon = block_km / (111.32 * max(math.cos(math.radians(centre)), 0.01))
    col = math.floor((lon + 180) / dlon)
    return f"b{block_km:g}km_r{row}_c{col}"


class _UnionFind:
    def __init__(self, keys):
        self.p = {k: k for k in keys}

    def find(self, k):
        while self.p[k] != k:
            self.p[k] = self.p[self.p[k]]
            k = self.p[k]
        return k

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[max(ra, rb)] = min(ra, rb)


# ---------- FR-072 overlap report ----------

def _pairs(gdf_a, gdf_b, same: bool):
    """Index pairs whose geometries intersect (each unordered pair once when same)."""
    import geopandas as gpd
    j = gpd.sjoin(gdf_a[["geometry"]].reset_index(names="_a"), gdf_b[["geometry"]].reset_index(names="_b"),
                  predicate="intersects")
    pairs = j[["_a", "_b"]].to_numpy()
    if same:
        pairs = pairs[pairs[:, 0] < pairs[:, 1]]
    return [tuple(p) for p in pairs]


def overlap_report(sites, labels=None):
    """Every pair of sites that share a scene, overlap in footprint, or overlap in labels.

    Returns a DataFrame with site_a, dataset_a, site_b, dataset_b, kind
    (same_scene | footprint_overlap | label_overlap), cross_dataset, overlap_km2 and iou.
    """
    import pandas as pd
    s = sites.reset_index(drop=True)
    if s["site_id"].duplicated().any():
        raise ValueError("site_id must be unique across the datasets passed together")
    ds = dict(zip(s["site_id"], s["dataset"]))
    rows = []

    def add(a, b, kind, inter_km2=None, iou=None):
        a, b = sorted((a, b))
        rows.append({"site_a": a, "dataset_a": ds[a], "site_b": b, "dataset_b": ds[b], "kind": kind,
                     "cross_dataset": ds[a] != ds[b], "overlap_km2": inter_km2, "iou": iou})

    for _, g in s.groupby("scene_id"):
        ids = sorted(g["site_id"])
        for i in range(len(ids)):
            for k in range(i + 1, len(ids)):
                add(ids[i], ids[k], "same_scene")

    def areas(geom_a, geom_b):
        import geopandas as gpd
        pair = gpd.GeoSeries([geom_a, geom_b], crs=4326)
        c = pair.iloc[:1].representative_point().iloc[0]
        pm = pair.to_crs(_utm_epsg(c.x, c.y))
        inter = pm.iloc[0].intersection(pm.iloc[1]).area
        union = pm.iloc[0].union(pm.iloc[1]).area
        return inter / 1e6, (inter / union if union else None)

    for i, k in _pairs(s, s, same=True):
        km2, iou = areas(s.geometry.iloc[i], s.geometry.iloc[k])
        if km2 > 0:
            add(s["site_id"].iloc[i], s["site_id"].iloc[k], "footprint_overlap", km2, iou)

    if labels is not None and len(labels):
        lab = labels[labels.geometry.notna() & ~labels.geometry.is_empty]
        lab = lab.dissolve(by="site_id").reset_index()[["site_id", "geometry"]]
        lab = lab[lab["site_id"].isin(ds)].reset_index(drop=True)
        for i, k in _pairs(lab, lab, same=True):
            a, b = lab["site_id"].iloc[i], lab["site_id"].iloc[k]
            if a == b:
                continue
            km2, iou = areas(lab.geometry.iloc[i], lab.geometry.iloc[k])
            if km2 > 0:
                add(a, b, "label_overlap", km2, iou)
    cols = ["site_a", "dataset_a", "site_b", "dataset_b", "kind", "cross_dataset", "overlap_km2", "iou"]
    return pd.DataFrame(rows, columns=cols).drop_duplicates(["site_a", "site_b", "kind"]).reset_index(drop=True)


# ---------- FR-077 units, splits and lock ----------

def build_units(sites, overlaps=None, block_km: float = 50.0, buffer_km: float = 10.0):
    """Assign block_id and unit_id. Joins sites by overlap, block and buffer distance."""
    s = sites.reset_index(drop=True).copy()
    c = s.geometry.representative_point()
    s["block_id"] = [block_id(x, y, block_km) for x, y in zip(c.x, c.y)]
    uf = _UnionFind(list(s["site_id"]))
    for _, g in s.groupby("block_id"):
        ids = list(g["site_id"])
        for k in ids[1:]:
            uf.union(ids[0], k)
    if overlaps is not None:
        for a, b in overlaps[["site_a", "site_b"]].itertuples(index=False):
            if a in uf.p and b in uf.p:
                uf.union(a, b)
    if buffer_km > 0:
        import geopandas as gpd
        half = gpd.GeoDataFrame({"site_id": s["site_id"]}, geometry=_buffered_4326(s, buffer_km * 500.0), crs=4326)
        for i, k in _pairs(half, half, same=True):
            uf.union(s["site_id"].iloc[i], s["site_id"].iloc[k])
    roots = [uf.find(k) for k in s["site_id"]]
    names = {r: f"u{n:04d}" for n, r in enumerate(sorted(set(roots)))}
    s["unit_id"] = [names[r] for r in roots]
    return s


def _lock_hash(site_ids) -> str:
    return hashlib.sha256("\n".join(sorted(site_ids)).encode()).hexdigest()


def read_lock(path) -> dict:
    """Read a test lock and refuse it if its site list no longer matches its hash."""
    lock = json.loads(Path(path).read_text())
    if _lock_hash(lock["site_ids"]) != lock["sha256"]:
        raise ValueError(f"locked test set {path} was edited: hash does not match its site list")
    return lock


def lock_test(split_sites, out_path, note: str = "") -> dict:
    """Freeze the current test sites. Refuses to overwrite an existing lock."""
    out = Path(out_path)
    if out.exists():
        raise FileExistsError(f"{out} exists; a locked test set is never overwritten")
    ids = sorted(split_sites.loc[split_sites["split"] == "test", "site_id"])
    if not ids:
        raise ValueError("no test sites to lock")
    lock = {"site_ids": ids, "count": len(ids), "sha256": _lock_hash(ids),
            "datasets": sorted(split_sites.loc[split_sites["split"] == "test", "dataset"].unique()),
            "locked_at": datetime.now(timezone.utc).isoformat(), "note": note}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(lock, indent=2))
    return lock


def assign_splits(sites, overlaps=None, fractions=(0.7, 0.15, 0.15), block_km: float = 50.0,
                  buffer_km: float = 10.0, seed: int = 0, lock=None, stratify: str | None = None):
    """Return sites with block_id, unit_id and split, plus a summary dict.

    Units are shuffled with `seed` and each goes to the split furthest below its
    target share of sites. With `lock` (a dict from read_lock), units holding a
    locked site go to test; their other sites are held out (split = HELD_OUT).
    With `stratify` (a column such as mine_scale), each unit goes to the split
    furthest below target for the unit's most common value of that column, so
    each split gets a similar mix. Overall shares break ties.
    """
    if len(fractions) != 3 or abs(sum(fractions) - 1) > 1e-6 or min(fractions) < 0:
        raise ValueError("fractions must be three non-negative numbers summing to 1 (train, val, test)")
    s = build_units(sites, overlaps, block_km, buffer_km)
    locked = set(lock["site_ids"]) if lock else set()
    missing = locked - set(s["site_id"])
    if missing:
        raise ValueError(f"{len(missing)} locked test site(s) are missing from the sites given, e.g. {sorted(missing)[:3]}")
    s["split"] = None
    sizes = s.groupby("unit_id").size()
    counts = {k: 0 for k in SPLITS}
    if locked:
        locked_units = set(s.loc[s["site_id"].isin(locked), "unit_id"])
        in_lu = s["unit_id"].isin(locked_units)
        s.loc[in_lu & s["site_id"].isin(locked), "split"] = "test"
        s.loc[in_lu & ~s["site_id"].isin(locked), "split"] = HELD_OUT
        counts["test"] = int(s["site_id"].isin(locked).sum())
    free = [u for u in sizes.index if s.loc[s["unit_id"] == u, "split"].isna().all()]
    rng = np.random.default_rng(seed)
    order = list(rng.permutation(free))
    # Large units first, so they do not overshoot a small split at the end.
    order.sort(key=lambda u: -sizes[u])
    active = s["split"] != HELD_OUT
    total = int(active.sum())
    target = dict(zip(SPLITS, fractions))
    strat = s[stratify].fillna("unknown").astype(str) if stratify else None
    unit_stratum = strat.groupby(s["unit_id"]).agg(lambda v: v.value_counts().index[0]) if stratify else None
    st_total = strat[active].value_counts().to_dict() if stratify else {}
    st_counts = {k: {} for k in SPLITS}
    if stratify and locked:
        for v in strat[s["split"] == "test"]:
            st_counts["test"][v] = st_counts["test"].get(v, 0) + 1
    for u in order:
        deficit = {k: target[k] * total - counts[k] for k in SPLITS}
        if stratify:
            g = unit_stratum[u]
            sdef = {k: target[k] * st_total.get(g, 0) - st_counts[k].get(g, 0) for k in SPLITS}
        else:
            sdef = deficit
        if locked:
            deficit["test"] = sdef["test"] = -math.inf  # a locked test set takes no new sites
        k = max(SPLITS, key=lambda x: (sdef[x], deficit[x], -SPLITS.index(x)))
        members = s["unit_id"] == u
        s.loc[members, "split"] = k
        counts[k] += int(sizes[u])
        if stratify:
            for v, n in strat[members].value_counts().items():
                st_counts[k][v] = st_counts[k].get(v, 0) + int(n)
    summary = {"sites": int(len(s)), "units": int(s["unit_id"].nunique()), "blocks": int(s["block_id"].nunique()),
               "block_km": block_km, "buffer_km": buffer_km, "seed": seed, "fractions": list(fractions),
               "by_split": {k: int(v) for k, v in s["split"].value_counts().items()},
               "largest_unit_sites": int(sizes.max()) if len(sizes) else 0,
               "locked_test_sites": len(locked), "stratify": stratify}
    if stratify:
        summary["by_split_and_stratum"] = {k: {str(a): int(b) for a, b in g.items()}
                                           for k, g in s.groupby("split")[stratify].apply(
                                               lambda v: v.fillna("unknown").value_counts()).unstack(fill_value=0).iterrows()}
    return s, summary


def check_split(split_sites, buffer_km: float = 10.0, overlaps=None, lock=None) -> dict:
    """Independent leakage test (ML-03): returns problems found, empty lists when clean.

    Checks that no site is in two splits, no unit spans splits, no two sites in
    different splits are within buffer_km, no overlap pair spans splits, and that
    every locked test site is still in test.
    """
    import geopandas as gpd
    s = split_sites.reset_index(drop=True)
    s = s[s["split"].isin(SPLITS)].reset_index(drop=True)
    out = {"duplicate_site_ids": sorted(s.loc[s["site_id"].duplicated(), "site_id"])}
    if "unit_id" in s:
        n = s.groupby("unit_id")["split"].nunique()
        out["units_in_two_splits"] = sorted(n[n > 1].index)
    near = []
    if buffer_km > 0 and len(s):
        half = gpd.GeoDataFrame({"site_id": s["site_id"]}, geometry=_buffered_4326(s, buffer_km * 500.0), crs=4326)
        sp = dict(zip(s["site_id"], s["split"]))
        for i, k in _pairs(half, half, same=True):
            a, b = s["site_id"].iloc[i], s["site_id"].iloc[k]
            if sp[a] != sp[b]:
                near.append((a, sp[a], b, sp[b]))
    out["sites_within_buffer_across_splits"] = near
    if overlaps is not None and len(overlaps):
        sp = dict(zip(s["site_id"], s["split"]))
        out["overlaps_across_splits"] = [(a, b, kind) for a, b, kind in overlaps[["site_a", "site_b", "kind"]].itertuples(index=False)
                                         if a in sp and b in sp and sp[a] != sp[b]]
    if lock:
        sp = dict(zip(split_sites["site_id"], split_sites["split"]))
        out["locked_not_in_test"] = sorted(i for i in lock["site_ids"] if sp.get(i) != "test")
        out["test_not_in_lock"] = sorted(set(s.loc[s["split"] == "test", "site_id"]) - set(lock["site_ids"]))
    out["ok"] = not any(v for k, v in out.items() if k != "ok")
    return out


def read_sites(paths):
    """Concatenate sites and labels from one or more sites.gpkg files written by ingest."""
    import geopandas as gpd
    import pandas as pd
    sites, labels = [], []
    for p in paths:
        sites.append(gpd.read_file(p, layer="sites"))
        try:
            labels.append(gpd.read_file(p, layer="labels"))
        except Exception:  # a sites file without a labels layer
            pass
    s = gpd.GeoDataFrame(pd.concat(sites, ignore_index=True), crs=4326)
    lab = gpd.GeoDataFrame(pd.concat(labels, ignore_index=True), crs=4326) if labels else None
    return s, lab
