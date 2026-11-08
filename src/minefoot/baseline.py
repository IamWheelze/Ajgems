"""Gate G1 baselines (FRD 8.3): a spectral-index threshold and a random forest on pixels.

Internal research only. Train sites fit the models, validation sites choose the
index and score thresholds, and test sites are scored once at the end. No
hyperparameter search (FRD 8.3: not before G2). Scores are "model scores", not
probabilities (FR-084).

Pixels the quality mask removed (NaN reflectance) are excluded from training and
from every metric, and the share of labelled ground they hide is reported
separately (FR-052). Inputs come only from `minefoot.preprocess` outputs (ML-02).
Neither model needs normalisation, so none is fitted (ML-06).
"""
from __future__ import annotations
import hashlib
import json
import platform
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from . import PREPROCESSING_VERSION
from .schema import BANDS, SCHEMA_VERSION

INDICES = ("NDVI", "NDWI", "MNDWI", "BSI", "NDBI")
FEATURES = (*BANDS, *INDICES)
# Index baselines: (feature, sign). Sign -1 means "low values look like disturbance".
INDEX_CANDIDATES = (("BSI", 1), ("NDBI", 1), ("NDVI", -1))
MMU_PIXELS = 100            # 1 ha at 10 m: FRD minimum mapping unit for evaluation
FALSE_CANDIDATE_MAX_LABEL_SHARE = 0.5   # a predicted patch with less labelled mining than this is a false candidate
FOOTPRINT_DEFINITION = ("v2: false candidate = predicted 8-connected patch of >= 1 ha whose usable pixels are "
                        "< 50% labelled mining; false_candidates_detached = such patches touching no label (v1 rule)")
SCORE_LEVELS = 256          # scores are stored as uint8 0..255
RF_PARAMS = dict(n_estimators=100, max_depth=20, min_samples_leaf=5, max_features="sqrt", n_jobs=-1)
PER_SITE_SAMPLE = 3000      # pixels per class per training site


# ---------- features ----------

def _nd(a, b):
    with np.errstate(invalid="ignore", divide="ignore"):
        d = a + b
        return np.where(np.abs(d) > 1e-6, (a - b) / d, np.nan).astype("float32")


def features(refl: np.ndarray) -> np.ndarray:
    """(10, H, W) reflectance in schema order to (15, H, W) features in FEATURES order."""
    b = {name: refl[i] for i, name in enumerate(BANDS)}
    idx = {"NDVI": _nd(b["B08"], b["B04"]), "NDWI": _nd(b["B03"], b["B08"]), "MNDWI": _nd(b["B03"], b["B11"]),
           "BSI": _nd(b["B11"] + b["B04"], b["B08"] + b["B02"]), "NDBI": _nd(b["B11"], b["B08"])}
    return np.concatenate([refl.astype("float32"), np.stack([idx[k] for k in INDICES])])


def load_site(prepared_dir, labels_path):
    """Features, valid-pixel mask, label mask and raster profile for one prepared site."""
    import rasterio
    from .labels import rasterize_to, read_vectors
    p = Path(prepared_dir)
    with rasterio.open(p / "reflectance.tif") as s:
        refl = s.read()
        profile = s.profile
    feats = features(refl)
    valid = np.isfinite(refl).all(axis=0)
    label = rasterize_to(p / "reflectance.tif", read_vectors(labels_path)).astype(bool)
    return feats, valid, label, profile


# ---------- models ----------

def index_score(feats: np.ndarray, name: str, sign: int) -> np.ndarray:
    """Map an index in [-1, 1] to a score in [0, 1] where higher looks more like disturbance."""
    v = feats[FEATURES.index(name)]
    return np.clip((sign * v + 1) / 2, 0, 1)


def sample_training_pixels(sites, seed=0, per_class=PER_SITE_SAMPLE, hard_negatives=None):
    """Balanced pixel sample: up to per_class mining and per_class other valid pixels per site.

    With `hard_negatives` = {"worldcover_root": ..., "per_type": n, "buffer_px": b, "types": codes}, also
    adds up to n pixels of each listed proxy negative type (negatives.PROXY_TYPES codes, default 1 to 4)
    per site, labelled 0.
    The random negatives are drawn first, as without hard negatives, so the two samples share them.
    """
    rng = np.random.default_rng(seed)
    X, y, n_from = [], [], {}
    for sid, prep, lab in sites:
        feats, valid, label, _ = load_site(prep, lab)
        flat = feats.reshape(feats.shape[0], -1)
        ok = valid.ravel() & np.isfinite(flat).all(axis=0)
        for cls, m in ((1, label.ravel() & ok), (0, ~label.ravel() & ok)):
            idx = np.flatnonzero(m)
            take = rng.choice(idx, size=min(per_class, idx.size), replace=False) if idx.size else idx
            X.append(flat[:, take].T)
            y.append(np.full(take.size, cls, "uint8"))
            n_from[(sid, cls)] = int(take.size)
        if hard_negatives:
            import rasterio
            from .negatives import PROXY_TYPES, fetch_worldcover, proxy_type_map
            wc_path = fetch_worldcover(prep, Path(hard_negatives["worldcover_root"]) / f"{sid}.tif")
            with rasterio.open(wc_path) as s:
                types = proxy_type_map(s.read(1), label, hard_negatives.get("buffer_px", 50)).ravel()
            hrng = np.random.default_rng([seed, int(hashlib.sha256(sid.encode()).hexdigest()[:8], 16)])
            for code in hard_negatives.get("types", (1, 2, 3, 4)):
                idx = np.flatnonzero((types == code) & ok)
                take = hrng.choice(idx, size=min(hard_negatives["per_type"], idx.size), replace=False) if idx.size else idx
                X.append(flat[:, take].T)
                y.append(np.zeros(take.size, "uint8"))
                n_from[(sid, PROXY_TYPES[code])] = int(take.size)
    return np.concatenate(X), np.concatenate(y), n_from


def fit_rf(X, y, seed=0):
    from sklearn.ensemble import RandomForestClassifier
    rf = RandomForestClassifier(random_state=seed, **RF_PARAMS)
    rf.fit(X, y)
    return rf


def rf_score(rf, feats: np.ndarray, valid: np.ndarray, chunk=500_000) -> np.ndarray:
    flat = feats.reshape(feats.shape[0], -1).T
    ok = valid.ravel() & np.isfinite(flat).all(axis=1)
    out = np.zeros(flat.shape[0], "float32")
    idx = np.flatnonzero(ok)
    for i in range(0, idx.size, chunk):
        j = idx[i:i + chunk]
        out[j] = rf.predict_proba(flat[j])[:, 1]
    return out.reshape(valid.shape)


def to_levels(score: np.ndarray) -> np.ndarray:
    return np.clip(np.round(score * (SCORE_LEVELS - 1)), 0, SCORE_LEVELS - 1).astype("uint8")


# ---------- metrics ----------

def histograms(levels: np.ndarray, valid: np.ndarray, label: np.ndarray) -> dict:
    """Counts of score levels for mining and other valid pixels: enough for any threshold."""
    pos = np.bincount(levels[valid & label], minlength=SCORE_LEVELS)
    neg = np.bincount(levels[valid & ~label], minlength=SCORE_LEVELS)
    return {"pos": pos.astype("int64"), "neg": neg.astype("int64")}


def counts_at(h: dict, t: int) -> dict:
    """Pixel confusion counts when level >= t is predicted as disturbance."""
    tp = int(h["pos"][t:].sum()); fn = int(h["pos"][:t].sum())
    fp = int(h["neg"][t:].sum()); tn = int(h["neg"][:t].sum())
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn}


def prf(c: dict) -> dict:
    tp, fp, fn = c["tp"], c["fp"], c["fn"]
    p = tp / (tp + fp) if tp + fp else float("nan")
    r = tp / (tp + fn) if tp + fn else float("nan")
    f = 2 * p * r / (p + r) if p == p and r == r and p + r else float("nan")
    iou = tp / (tp + fp + fn) if tp + fp + fn else float("nan")
    return {"precision": p, "recall": r, "f1": f, "iou": iou}


def best_threshold(hists: list[dict]) -> tuple[int, float]:
    """Level that maximises pooled pixel F1 over the given sites (used on validation only)."""
    pos = sum(h["pos"] for h in hists); neg = sum(h["neg"] for h in hists)
    tp = np.cumsum(pos[::-1])[::-1]; fp = np.cumsum(neg[::-1])[::-1]; fn = pos.sum() - tp
    with np.errstate(invalid="ignore", divide="ignore"):
        f1 = np.where(2 * tp + fp + fn > 0, 2 * tp / (2 * tp + fp + fn), 0)
    t = int(np.nanargmax(f1[1:]) + 1)
    return t, float(f1[t])


def object_metrics(pred: np.ndarray, valid: np.ndarray, label: np.ndarray, mmu=MMU_PIXELS) -> dict:
    """Footprint-level counts. Labelled footprints are 8-connected parts of the label of at least
    `mmu` usable pixels. A footprint is found if any usable pixel in it is predicted, and covered if
    at least 25% are.

    False candidates are predicted parts of at least `mmu` usable pixels that are less than half
    labelled mining (FOOTPRINT_DEFINITION v2). This counts large false areas that merely touch a mine,
    which the v1 rule (touching no label, kept as false_candidates_detached) missed."""
    from scipy import ndimage
    st = np.ones((3, 3), bool)
    lab_cc, n_lab = ndimage.label(label & valid, structure=st)
    sizes = np.bincount(lab_cc.ravel(), minlength=n_lab + 1)
    hit = np.bincount(lab_cc[pred & valid].ravel(), minlength=n_lab + 1)
    big = np.flatnonzero(sizes >= mmu); big = big[big > 0]
    found = int((hit[big] > 0).sum()); covered = int((hit[big] >= 0.25 * sizes[big]).sum())
    pr_cc, n_pr = ndimage.label(pred & valid, structure=st)
    psize = np.bincount(pr_cc.ravel(), minlength=n_pr + 1)
    touch = np.bincount(pr_cc[label].ravel(), minlength=n_pr + 1)
    pbig = np.flatnonzero(psize >= mmu); pbig = pbig[pbig > 0]
    share = touch[pbig] / psize[pbig]
    false = pbig[share < FALSE_CANDIDATE_MAX_LABEL_SHARE]
    return {"label_footprints": int(big.size), "found_any_overlap": found, "covered_25pct": covered,
            "predicted_parts": int(pbig.size), "false_candidates": int(false.size),
            "false_candidates_detached": int((touch[pbig] == 0).sum()),
            "false_candidate_ha": float(psize[false].sum() * 0.01),
            "valid_km2": float(valid.sum() * 1e-4)}


def summarise(rows: list[dict], n_boot=1000, seed=0) -> dict:
    """Pooled metrics over sites with a site bootstrap 95% interval (FRD 12: pixels are not independent)."""
    keys = ("tp", "fp", "fn", "tn", "label_footprints", "found_any_overlap", "covered_25pct",
            "false_candidates", "false_candidates_detached", "false_candidate_ha", "valid_km2",
            "label_pixels", "label_pixels_masked")
    if not rows:
        return {"sites": 0}
    M = np.array([[r.get(k, np.nan) for k in keys] for r in rows], dtype="float64")   # older runs lack new keys

    def stats(tot):
        t = dict(zip(keys, tot))
        m = prf({k: t[k] for k in ("tp", "fp", "fn")})
        m["footprint_recall_any"] = t["found_any_overlap"] / t["label_footprints"] if t["label_footprints"] else float("nan")
        m["footprint_recall_25pct"] = t["covered_25pct"] / t["label_footprints"] if t["label_footprints"] else float("nan")
        m["false_candidates_per_1000km2"] = 1000 * t["false_candidates"] / t["valid_km2"] if t["valid_km2"] else float("nan")
        m["false_candidates_detached_per_1000km2"] = (1000 * t["false_candidates_detached"] / t["valid_km2"]
                                                      if t["valid_km2"] else float("nan"))
        m["false_candidate_ha_per_1000km2"] = 1000 * t["false_candidate_ha"] / t["valid_km2"] if t["valid_km2"] else float("nan")
        # Area flagged outside labels, km2 per 1,000 km2 of usable area. Unlike the false-candidate count,
        # it also counts large false areas attached to a labelled mine.
        m["false_alarm_km2_per_1000km2"] = 1000 * t["fp"] * 1e-4 / t["valid_km2"] if t["valid_km2"] else float("nan")
        m["label_pixels_masked_share"] = t["label_pixels_masked"] / t["label_pixels"] if t["label_pixels"] else float("nan")
        return m

    point = stats(M.sum(0))
    if n_boot == 0:
        return {"sites": len(rows), **{k: float(v) for k, v in point.items()}}
    rng = np.random.default_rng(seed)
    boots = [stats(M[rng.integers(0, len(M), len(M))].sum(0)) for _ in range(n_boot)]
    ci = {k: [float(np.nanpercentile([b[k] for b in boots], 2.5)), float(np.nanpercentile([b[k] for b in boots], 97.5))]
          for k in point if k != "label_pixels_masked_share"}
    tot = dict(zip(keys, M.sum(0)))
    return {"sites": len(rows), **{k: float(v) for k, v in point.items()}, "ci95": ci,
            "totals": {k: float(v) for k, v in tot.items()}}


# Higher is better for all of these except false candidates.
COMPARE_METRICS = ("f1", "precision", "recall", "iou", "footprint_recall_25pct", "false_candidates_per_1000km2",
                   "false_candidate_ha_per_1000km2", "false_alarm_km2_per_1000km2")
LOWER_IS_BETTER = {"false_candidates_per_1000km2", "false_candidate_ha_per_1000km2", "false_alarm_km2_per_1000km2"}


def paired_compare(rows_a: list[dict], rows_b: list[dict], metrics=COMPARE_METRICS, n_boot=2000, seed=0) -> dict:
    """Paired site bootstrap of B minus A on the same sites (FRD 8.3 G2, FR-103).

    Each resample draws the same sites for both models, so the interval is for the
    difference itself. B "beats" A on a metric when the 95% interval of the
    difference lies wholly on the better side of zero.
    """
    ka = {r["site_id"]: r for r in rows_a}; kb = {r["site_id"]: r for r in rows_b}
    if set(ka) != set(kb):
        raise ValueError(f"models were scored on different sites: {sorted(set(ka) ^ set(kb))[:5]}")
    ids = sorted(ka)
    A = [ka[i] for i in ids]; B = [kb[i] for i in ids]
    diff = [i for i in ids if "label_pixels" in ka[i] and "label_pixels" in kb[i]
            and (ka[i]["label_pixels"], ka[i]["tp"] + ka[i]["fn"], ka[i]["fp"] + ka[i]["tn"])
            != (kb[i]["label_pixels"], kb[i]["tp"] + kb[i]["fn"], kb[i]["fp"] + kb[i]["tn"])]
    if diff:
        raise ValueError(f"{len(diff)} site(s) differ in labelled or usable pixels between the two runs, "
                         f"so they were not scored on the same data, e.g. {diff[:3]}")
    rng = np.random.default_rng(seed)
    point_a, point_b = summarise(A, n_boot=0), summarise(B, n_boot=0)
    draws = {m: [] for m in metrics}
    for _ in range(n_boot):
        idx = rng.integers(0, len(ids), len(ids))
        sa = summarise([A[i] for i in idx], n_boot=0); sb = summarise([B[i] for i in idx], n_boot=0)
        for m in metrics:
            draws[m].append(sb[m] - sa[m])
    out = {"sites": len(ids), "n_boot": n_boot, "metrics": {}}
    for m in metrics:
        d = np.array(draws[m], dtype="float64")
        lo, hi = float(np.nanpercentile(d, 2.5)), float(np.nanpercentile(d, 97.5))
        better = (hi < 0) if m in LOWER_IS_BETTER else (lo > 0)
        worse = (lo > 0) if m in LOWER_IS_BETTER else (hi < 0)
        out["metrics"][m] = {"a": point_a[m], "b": point_b[m], "b_minus_a": point_b[m] - point_a[m],
                             "ci95": [lo, hi], "verdict": "b better" if better else "b worse" if worse else "no clear difference"}
    return out


# ---------- run ----------

def _git_commit():
    try:
        c = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], capture_output=True,
                               text=True).stdout.strip()
        return c + ("+uncommitted" if dirty else "")
    except Exception:
        return None


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_score_map(levels, profile, path):
    import rasterio
    p = {**profile, "count": 1, "dtype": "uint8", "nodata": None, "compress": "deflate", "tiled": True,
         "blockxsize": 256, "blockysize": 256}
    with rasterio.open(path, "w", **p) as d:
        d.write(levels, 1)


def write_error_map(pred, valid, label, profile, path):
    """0 correct background, 1 true positive, 2 false positive, 3 missed (false negative), 4 not assessed."""
    import rasterio
    e = np.zeros(valid.shape, "uint8")
    e[pred & label] = 1; e[pred & ~label] = 2; e[~pred & label] = 3; e[~valid] = 4
    p = {**profile, "count": 1, "dtype": "uint8", "nodata": None, "compress": "deflate", "tiled": True,
         "blockxsize": 256, "blockysize": 256}
    with rasterio.open(path, "w", **p) as d:
        d.write(e, 1)
        d.write_colormap(1, {0: (0, 0, 0, 0), 1: (0, 200, 90, 255), 2: (255, 140, 0, 255),
                             3: (220, 0, 120, 255), 4: (90, 90, 90, 255)})


def check_inputs(sites_by_split: dict, split_gpkg, prepared_root, labels_root):
    """Refuse a run whose site lists disagree with the split file or whose sites are not prepared.

    Returns (meta indexed by site_id, region by site_id, paths function).
    """
    import geopandas as gpd
    from .datasets import _region
    from .splits import check_split
    split = gpd.read_file(split_gpkg, layer="sites")
    leak = check_split(split, buffer_km=0)
    if not leak["ok"]:
        raise RuntimeError(f"split file fails the leakage check: {leak}")
    truth = dict(zip(split["site_id"], split["split"]))
    for k, ids in sites_by_split.items():
        wrong = [i for i in ids if truth.get(i) != k]
        if wrong:
            raise ValueError(f"{len(wrong)} site(s) listed under {k} are not in that split, e.g. {wrong[:3]}")
    meta = split.set_index("site_id")
    c = split.to_crs(3857).geometry.centroid.to_crs(4326)
    region = dict(zip(split["site_id"], [_region(x, y) for x, y in zip(c.x, c.y)]))
    paths = lambda ids: [(i, Path(prepared_root) / i, Path(labels_root) / i / "labels.geojson") for i in ids]
    for k, ids in sites_by_split.items():
        missing = [i for i, p, l in paths(ids) if not (p / "prepared.json").exists() or not l.exists()]
        if missing:
            raise FileNotFoundError(f"{len(missing)} {k} site(s) not prepared, e.g. {missing[:3]}")
    return meta, region, paths


def base_config(gate, sites_by_split, split_gpkg, seed) -> dict:
    return {"gate": gate, "purpose": "internal research; not for release",
            "created_at": datetime.now(timezone.utc).isoformat(), "code_commit": _git_commit(),
            "dataset": {"name": "global_ml_ready", "version": "1.0.0", "licence": "CC BY-SA 4.0",
                        "label_layer": "preferred_polygons"},
            "split_file": str(split_gpkg), "split_file_sha256": _sha256(split_gpkg),
            "sites": {k: sorted(v) for k, v in sites_by_split.items()},
            "input_schema": SCHEMA_VERSION, "preprocessing_version": PREPROCESSING_VERSION, "seed": seed,
            "mmu_pixels": MMU_PIXELS, "threshold_rule": "pooled pixel F1 on validation sites",
            "environment": {"python": sys.version.split()[0], "platform": platform.platform(),
                            "numpy": np.__version__}}


def score_held_out(out, scorers: dict, sites_by_split, prepared_root, labels_root, log=print, t0=None) -> dict:
    """Score every validation and test site with each scorer(feats, valid) -> score in [0, 1].

    Writes uint8 score maps to out/scores and returns the state evaluate_scores needs.
    """
    t0 = t0 or time.time()
    hists = {m: {} for m in scorers}
    masks, profiles, masked = {}, {}, {}
    maps_dir = Path(out) / "scores"; maps_dir.mkdir(parents=True, exist_ok=True)
    held = [("val", i) for i in sites_by_split["val"]] + [("test", i) for i in sites_by_split["test"]]
    for n, (k, sid) in enumerate(held, 1):
        feats, valid, label, profile = load_site(Path(prepared_root) / sid, Path(labels_root) / sid / "labels.geojson")
        for m, fn in scorers.items():
            lv = to_levels(np.where(valid, fn(feats, valid), 0))
            hists[m][sid] = histograms(lv, valid, label)
            write_score_map(lv, profile, maps_dir / f"{sid}__{m}.tif")
        masks[sid] = (np.packbits(valid), np.packbits(label), valid.shape)
        profiles[sid] = profile
        masked[sid] = (int(label.sum()), int((label & ~valid).sum()))
        log(f"scored {n}/{len(held)} {k} {sid} ({time.time() - t0:.0f}s)")
    return {"hists": hists, "masks": masks, "profiles": profiles, "masked": masked}


def choose_thresholds(state, sites_by_split) -> dict:
    """Threshold per model, chosen on validation only."""
    return {m: best_threshold([h[i] for i in sites_by_split["val"]]) for m, h in state["hists"].items()}


def evaluate_scores(out, report_models, chosen, state, sites_by_split, meta, region, seed=0) -> dict:
    """Metrics on validation and test at the validation-chosen thresholds; error maps for test."""
    import rasterio
    out = Path(out)
    err_dir = out / "error_maps"; err_dir.mkdir(exist_ok=True)
    results = {}
    for m in report_models:
        t = chosen[m][0]
        per_split = {}
        for k in ("val", "test"):
            rows = []
            for sid in sites_by_split[k]:
                pv, pl, shape = state["masks"][sid]
                n_px = shape[0] * shape[1]
                valid = np.unpackbits(pv)[:n_px].reshape(shape).astype(bool)
                label = np.unpackbits(pl)[:n_px].reshape(shape).astype(bool)
                with rasterio.open(out / "scores" / f"{sid}__{m}.tif") as src:
                    pred = (src.read(1) >= t) & valid
                obj = object_metrics(pred, valid, label)
                lp, lm = state["masked"][sid]
                rows.append({"site_id": sid, **counts_at(state["hists"][m][sid], t), **obj, "label_pixels": lp,
                             "label_pixels_masked": lm, "mine_scale": meta.loc[sid, "mine_scale"],
                             "mine_type": meta.loc[sid, "mine_type"], "region": region[sid]})
                if k == "test":
                    write_error_map(pred, valid, label, state["profiles"][sid], err_dir / f"{sid}__{m}.tif")
            strata = {"all": summarise(rows, seed=seed)}
            for col in ("mine_scale", "region", "mine_type"):
                for v in sorted({r[col] for r in rows}):
                    strata[f"{col}={v}"] = summarise([r for r in rows if r[col] == v], seed=seed)
            per_split[k] = {"strata": strata, "per_site": rows}
        results[m] = {"threshold_level": t, "threshold_score": t / (SCORE_LEVELS - 1),
                      "validation_f1_at_choice": chosen[m][1], **per_split}
    return results


def add_type_breakdown(out, metrics, results, prepared_root, labels_root, worldcover_root):
    """ML-08: per-type false-alert rates (WorldCover proxy types) for every reported model."""
    from .negatives import evaluate_run_by_type
    by_type = {f"{m}/{k}": evaluate_run_by_type(out, m, k, prepared_root, labels_root, worldcover_root)
               for m in results for k in ("val", "test")}
    (Path(out) / "false_alerts_by_type.json").write_text(json.dumps(by_type, indent=2))
    metrics["false_alerts_by_type"] = {k: v["by_type"] for k, v in by_type.items()}


def run_g1(sites_by_split: dict, split_gpkg, prepared_root, labels_root, out_dir, seed=0, log=print,
           hard_negatives: dict | None = None, worldcover_root=None) -> dict:
    """Run both G1 baselines end to end and write a lineage record (FR-101, ML-09)."""
    import joblib
    out = Path(out_dir); out.mkdir(parents=True, exist_ok=True)
    meta, region, paths = check_inputs(sites_by_split, split_gpkg, prepared_root, labels_root)
    config = base_config("G1", sites_by_split, split_gpkg, seed)
    config.update({"features": list(FEATURES), "normalisation": "none (threshold and tree models are scale-invariant)",
                   "index_candidates": [list(x) for x in INDEX_CANDIDATES], "rf_params": RF_PARAMS,
                   "per_site_sample_per_class": PER_SITE_SAMPLE,
                   "hard_negatives": ({**{k: str(v) for k, v in hard_negatives.items()},
                                       "source": "ESA WorldCover v200 (2021) proxy, CC BY 4.0"} if hard_negatives else None)})
    try:
        import sklearn, rasterio as rio
        config["environment"].update(sklearn=sklearn.__version__, rasterio=rio.__version__)
    except Exception:
        pass
    (out / "config.json").write_text(json.dumps(config, indent=2))

    t0 = time.time()
    log(f"sampling training pixels from {len(sites_by_split['train'])} sites")
    X, y, n_from = sample_training_pixels(paths(sites_by_split["train"]), seed, hard_negatives=hard_negatives)
    hn_counts = {}
    for (sid, kind), n in n_from.items():
        if isinstance(kind, str):
            hn_counts[kind] = hn_counts.get(kind, 0) + n
    log(f"training random forest on {len(y):,} pixels ({int(y.sum()):,} mining)")
    rf = fit_rf(X, y, seed)
    joblib.dump(rf, out / "rf.joblib", compress=3)
    importance = dict(sorted(zip(FEATURES, map(float, rf.feature_importances_)), key=lambda t: -t[1]))

    scorers = {f"index_{n}{'_neg' if s < 0 else ''}": (lambda f, v, n=n, s=s: index_score(f, n, s))
               for n, s in INDEX_CANDIDATES}
    scorers["random_forest"] = lambda f, v: rf_score(rf, f, v)
    state = score_held_out(out, scorers, sites_by_split, prepared_root, labels_root, log, t0)

    chosen = choose_thresholds(state, sites_by_split)
    best_index = max((m for m in scorers if m.startswith("index_")), key=lambda m: chosen[m][1])
    log("validation F1: " + ", ".join(f"{m} {chosen[m][1]:.3f} at level {chosen[m][0]}" for m in scorers))
    results = evaluate_scores(out, (best_index, "random_forest"), chosen, state, sites_by_split, meta, region, seed)
    metrics = {"models": results, "validation_choice": {m: {"level": chosen[m][0], "f1": chosen[m][1]} for m in scorers},
               "best_index": best_index, "rf_feature_importance": importance,
               "training_pixels": int(len(y)), "training_mining_pixels": int(y.sum()),
               "training_hard_negative_pixels": hn_counts, "runtime_s": round(time.time() - t0, 1)}
    (out / "metrics.json").write_text(json.dumps(metrics, indent=2, default=float))
    if worldcover_root:
        add_type_breakdown(out, metrics, results, prepared_root, labels_root, worldcover_root)
    return metrics


def rescore_run(run_dir, prepared_root, labels_root, seed=0, log=print) -> dict:
    """Recompute footprint metrics of a stored run from its score maps, at its original thresholds.

    Pixel counts and thresholds are unchanged; only footprint metrics follow the current
    FOOTPRINT_DEFINITION. The previous metrics.json is kept as metrics_before_rescore_<n>.json.
    """
    import rasterio
    run_dir = Path(run_dir)
    m = json.loads((run_dir / "metrics.json").read_text())
    n = len(list(run_dir.glob("metrics_before_rescore_*.json"))) + 1
    (run_dir / f"metrics_before_rescore_{n}.json").write_text(json.dumps(m, indent=2, default=float))
    rows_by_site = {}
    for name, r in m["models"].items():
        for k in ("val", "test"):
            for row in r[k]["per_site"]:
                rows_by_site.setdefault(row["site_id"], []).append((name, row))
    for i, (sid, entries) in enumerate(sorted(rows_by_site.items()), 1):
        _, valid, label, _ = load_site(Path(prepared_root) / sid, Path(labels_root) / sid / "labels.geojson")
        for name, row in entries:
            with rasterio.open(run_dir / "scores" / f"{sid}__{name}.tif") as src:
                pred = (src.read(1) >= m["models"][name]["threshold_level"]) & valid
            row.update(object_metrics(pred, valid, label))
        log(f"rescored {i}/{len(rows_by_site)} {sid}")
    for name, r in m["models"].items():
        for k in ("val", "test"):
            rows = r[k]["per_site"]
            strata = {"all": summarise(rows, seed=seed)}
            for col in ("mine_scale", "region", "mine_type"):
                for v in sorted({x[col] for x in rows}):
                    strata[f"{col}={v}"] = summarise([x for x in rows if x[col] == v], seed=seed)
            r[k]["strata"] = strata
    m.setdefault("rescored", []).append({"at": datetime.now(timezone.utc).isoformat(), "code_commit": _git_commit(),
                                         "footprint_definition": FOOTPRINT_DEFINITION})
    (run_dir / "metrics.json").write_text(json.dumps(m, indent=2, default=float))
    return m
