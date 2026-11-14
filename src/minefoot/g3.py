"""Gate G3 (FRD 8.3): first test on Nigerian sites, with no Nigerian training.

Scores a finished model on the locked Nigerian test sites and measures the gap to its
global test performance. The rules (ML-07):

- The labels must match their lock; any edited or removed locked site stops the run.
- The threshold is the one chosen on GLOBAL validation. Nothing is tuned on these sites.
- Every run is appended to an access log with the lock hash.

Each site is scored inside a window: the bounding box of its polygons plus `window_m`
(default 1 km). The labelling protocol asks for all visible mining within 1 km of a site
to be labelled, so predictions in the window that miss every label are false alarms.
`ignore` polygons and masked pixels are excluded. Predictions outside all windows are
reported as unverified candidates, not as errors: nobody labelled there.
"""
from __future__ import annotations
import csv
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from .baseline import MMU_PIXELS, SCORE_LEVELS, _git_commit, histograms, object_metrics, summarise, to_levels

ACCESS_LOG_FIELDS = ["at", "run", "model_version", "code_commit", "lock_file", "lock_sha256", "sites", "purpose"]


def _region_for_scene(kit) -> dict:
    return {v["scene_id"]: k for k, v in kit["regions"].items()}


def site_window(geoms, transform, shape, window_m: float):
    """Pixel window (row0, row1, col0, col1) around a site's polygons, in the raster's CRS."""
    from shapely.ops import unary_union
    minx, miny, maxx, maxy = unary_union(list(geoms)).buffer(window_m).bounds
    inv = ~transform
    c0, r0 = inv * (minx, maxy); c1, r1 = inv * (maxx, miny)
    r0, c0 = max(int(np.floor(r0)), 0), max(int(np.floor(c0)), 0)
    r1, c1 = min(int(np.ceil(r1)), shape[0]), min(int(np.ceil(c1)), shape[1])
    return r0, r1, c0, c1


def site_found(pred, valid, mining, mmu=MMU_PIXELS) -> tuple[bool, bool, int]:
    """FRD O1: is a site of >= 1 ha found (any overlap) and covered (>= 25%)? Returns (found, covered, pixels)."""
    m = mining & valid
    n = int(m.sum())
    hit = int((pred & m).sum())
    return hit > 0, n > 0 and hit >= 0.25 * n, n


def append_access_log(log_path, row: dict):
    """ML-07: every use of the locked Nigerian set is recorded."""
    log_path = Path(log_path)
    new = not log_path.exists()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=ACCESS_LOG_FIELDS)
        if new:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in ACCESS_LOG_FIELDS})


def run_g3(run_dir, kit_dir, labels_path, lock_path, out_dir, negatives_path=None, window_m=1000.0,
           access_log="docs/evidence/nigeria_test_access_log.csv", device=None, seed=0, log=print) -> dict:
    import geopandas as gpd
    import rasterio
    from rasterio.features import rasterize
    from .g2 import load_run_model, predict_tile
    from .labels import polygon_mask_report
    from .labelling_kit import LABEL_LAYER, verify_lock
    from .schema import BANDS

    run_dir, kit_dir, out = Path(run_dir), Path(kit_dir), Path(out_dir)
    v = verify_lock(labels_path, lock_path)
    if not v["intact"]:
        raise ValueError(f"locked Nigerian labels changed since locking, so G3 cannot run: {v}")
    lock = json.loads(Path(lock_path).read_text())
    kit = json.loads((kit_dir / "kit.json").read_text())
    region_of = _region_for_scene(kit)
    labels = gpd.read_file(labels_path, layer=LABEL_LAYER)
    labels = labels[labels["site_id"].isin(lock["site_ids"])]   # sites added after locking are not test sites
    unknown = sorted(set(labels["scene_id"]) - set(region_of))
    if unknown:
        raise ValueError(f"labels on scenes that are not in the kit: {unknown[:3]}")
    model, norm, level, cfg, dev = load_run_model(run_dir, device)
    threshold = level / (SCORE_LEVELS - 1)
    out.mkdir(parents=True, exist_ok=True)
    rows, neg_rows, regions_out, site_hists = [], [], {}, []
    global_metrics = json.loads((run_dir / "metrics.json").read_text())["models"]["unet"]

    for region in sorted({region_of[s] for s in labels["scene_id"]}):
        prep = kit_dir / "prepared" / region
        with rasterio.open(prep / "reflectance.tif") as src:
            refl = src.read(); tr, crs, prof = src.transform, src.crs, src.profile
        valid = np.isfinite(refl).all(axis=0)
        log(f"{region}: scoring {refl.shape[1]} x {refl.shape[2]} pixels")
        score = predict_tile(model, refl[:len(BANDS)], norm, dev, cfg["chip"], cfg.get("infer_overlap") or cfg["chip"] // 4)
        lv = to_levels(score)
        p = {**prof, "count": 1, "dtype": "uint8", "nodata": None, "compress": "deflate", "tiled": True,
             "blockxsize": 256, "blockysize": 256}
        with rasterio.open(out / f"{region}_score.tif", "w", **p) as d:
            d.write(lv, 1)
        pred = (lv >= level) & valid
        lab = labels[labels["scene_id"].map(region_of) == region].to_crs(crs)
        mining_all = rasterize([(g, 1) for g in lab[lab["label_class"] == "mining_disturbance"].geometry],
                               out_shape=valid.shape, transform=tr, dtype="uint8").astype(bool)
        ignore_all = rasterize([(g, 1) for g in lab[lab["label_class"] == "ignore"].geometry] or [],
                               out_shape=valid.shape, transform=tr, dtype="uint8").astype(bool) \
            if (lab["label_class"] == "ignore").any() else np.zeros_like(valid)
        evaluated = np.zeros_like(valid)
        errmap = np.zeros(valid.shape, "uint8")      # 0 none, 1 hit, 2 false alarm, 3 missed, 4 not assessed, 5 unverified
        for sid, part in lab.groupby("site_id"):
            # Protocol v0.2: the window and the site's own extent come from its `site` polygons; `context`
            # polygons (labelled surroundings) count through mining_all and ignore_all but never move the window.
            if "role" in part.columns:
                part = part[part["role"].fillna("site") != "context"]
            r0, r1, c0, c1 = site_window(part.geometry, tr, valid.shape, window_m)
            win = (slice(r0, r1), slice(c0, c1))
            usable = valid[win] & ~ignore_all[win]
            m, pr = mining_all[win], pred[win] & usable
            evaluated[win] = True
            tp = int((pr & m).sum()); fp = int((pr & ~m).sum()); fn = int((~pr & m & usable).sum())
            tn = int((~pr & ~m & usable).sum())
            obj = object_metrics(pr, usable, m & usable)
            own = rasterize([(g, 1) for g in part[part["label_class"] == "mining_disturbance"].geometry],
                            out_shape=valid.shape, transform=tr, dtype="uint8").astype(bool)[win]
            found, covered, npx = site_found(pr, usable, own)
            mask = polygon_mask_report(prep, part[part["label_class"] == "mining_disturbance"].to_crs(4326))
            scale = part["mine_scale"].mode().iloc[0] if part["mine_scale"].notna().any() else "unknown"
            rows.append({"site_id": sid, "region": region, "mine_scale": scale, "mine_type": "unknown",
                         "tp": tp, "fp": fp, "fn": fn, "tn": tn, **obj,
                         "label_pixels": int(m.sum()), "label_pixels_masked": int((m & ~valid[win]).sum()),
                         "site_pixels": npx, "site_ha": npx * abs(tr.a * tr.e) / 1e4,
                         "site_found": found, "site_covered_25pct": covered,
                         "site_masked_fraction": float(np.nanmean(mask["masked_fraction"])) if len(mask) else None})
            h = histograms(lv[win], usable, m & usable)          # for the calibration check: no fitting here
            site_hists.append((rows[-1], h["pos"], h["neg"]))
            e = errmap[win]
            e[pr & m] = 1; e[pr & ~m] = 2; e[~pr & m & usable] = 3; e[~valid[win]] = 4
        outside = pred & ~evaluated
        errmap[outside] = 5
        with rasterio.open(out / f"{region}_errors.tif", "w", **p) as d:
            d.write(errmap, 1)
            d.write_colormap(1, {0: (0, 0, 0, 0), 1: (0, 200, 90, 255), 2: (255, 140, 0, 255), 3: (220, 0, 120, 255),
                                 4: (90, 90, 90, 255), 5: (86, 180, 233, 255)})
        px_km2 = abs(tr.a * tr.e) / 1e6
        regions_out[region] = {"scene_id": kit["regions"][region]["scene_id"],
                               "evaluated_km2": float(evaluated.sum() * px_km2),
                               "unverified_candidate_km2": float(outside.sum() * px_km2),
                               "usable_km2": float(valid.sum() * px_km2)}
        if negatives_path:   # ML-08 with real, digitised hard negatives
            negs = gpd.read_file(negatives_path, layer="hard_negatives")
            negs = negs[negs["scene_id"] == kit["regions"][region]["scene_id"]].to_crs(crs)
            for _, n in negs.iterrows():
                nm = rasterize([(n.geometry, 1)], out_shape=valid.shape, transform=tr, dtype="uint8").astype(bool) & valid
                if nm.any():
                    neg_rows.append({"region": region, "neg_type": n["neg_type"], "pixels": int(nm.sum()),
                                     "flagged": int((pred & nm).sum())})

    if not rows:
        raise ValueError("no locked mining sites found on the kit's scenes")
    strata = {"all": summarise(rows, seed=seed)}
    for col in ("region", "mine_scale"):
        for val in sorted({r[col] for r in rows}):
            strata[f"{col}={val}"] = summarise([r for r in rows if r[col] == val], seed=seed)
    big = [r for r in rows if r["site_ha"] >= 1.0]
    rng = np.random.default_rng(seed)
    def recall(rs, key):
        return float(np.mean([r[key] for r in rs])) if rs else float("nan")
    boots = [recall([big[i] for i in rng.integers(0, len(big), len(big))], "site_found") for _ in range(1000)] if big else []
    site_recall = {"sites_1ha_plus": len(big), "found_any_overlap": recall(big, "site_found"),
                   "found_ci95": [float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5))] if boots else None,
                   "covered_25pct": recall(big, "site_covered_25pct"), "frd_o1_target": 0.70}
    neg_summary = {}
    for t in sorted({n["neg_type"] for n in neg_rows}):
        rs = [n for n in neg_rows if n["neg_type"] == t]
        neg_summary[t] = {"polygons": len(rs), "pixels": sum(n["pixels"] for n in rs),
                          "flagged_share": sum(n["flagged"] for n in rs) / max(sum(n["pixels"] for n in rs), 1)}
    # FRD 8.5: measure, never fit, the global-validation calibration maps on these sites (ML-07).
    from .calibration import measure_on_sites, stored_tables
    st = stored_tables(run_dir)
    calibration = ({"fitted_on": st["fitted_on"], "applied_method": st["applied_method"],
                    "rule": "maps fitted on global validation; only measured here, never fitted on Nigerian sites",
                    "measured": measure_on_sites(site_hists, st["tables"], cols=("region", "mine_scale"), seed=seed)}
                   if st else None)
    g_test = global_metrics["test"]["strata"]["all"]
    result = {
        "gate": "G3", "purpose": "first Nigerian test, no Nigerian training", "run": str(run_dir),
        "model_version": f"{run_dir.name}:unet", "threshold_score": threshold,
        "threshold_rule": "chosen on GLOBAL validation; not tuned on Nigerian sites (ML-07)",
        "lock_sha256": lock["sha256"], "sites": len(rows), "window_m": window_m,
        "created_at": datetime.now(timezone.utc).isoformat(), "code_commit": _git_commit(),
        "strata": strata, "site_recall": site_recall, "regions": regions_out, "hard_negatives": neg_summary,
        "calibration": calibration,
        "global_test": {"f1": g_test["f1"], "f1_ci95": g_test["ci95"]["f1"], "precision": g_test["precision"],
                        "recall": g_test["recall"]},
        "gap_f1_nigeria_minus_global": strata["all"]["f1"] - g_test["f1"],
        "note": "Global and Nigerian sites differ, so the gap is between two independent samples, not a paired difference.",
    }
    (out / "g3_metrics.json").write_text(json.dumps(result, indent=2, default=float))
    with open(out / "g3_per_site.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
    if access_log:
        append_access_log(access_log, {"at": result["created_at"], "run": "G3", "model_version": result["model_version"],
                                        "code_commit": result["code_commit"], "lock_file": str(lock_path),
                                        "lock_sha256": lock["sha256"], "sites": len(rows),
                                        "purpose": "G3 test (no training, no threshold selection)"})
    return result
