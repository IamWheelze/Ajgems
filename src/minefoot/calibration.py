"""Score calibration for a stored run (FRD section 8.5, gate G5).

A model score is not a probability. This module fits a map from score level to the share of pixels that are labelled
mining, on the validation sites only, and then measures on the held-out test sites whether that map holds:
a reliability table, expected calibration error (ECE) and Brier score, per stratum, with intervals from resampling sites.

Rules kept here:
- The map is fitted on validation sites only. Test sites are used once, to measure it.
- The applied method is fixed in advance: Platt scaling (two parameters on the logit of the score), because pixels in a
  site are strongly correlated and the effective sample is the number of sites, not pixels. Isotonic regression is
  reported alongside as a check, never chosen by its test result.
- Both maps are monotone, so the order of pixels, and therefore every threshold-based metric, is unchanged.
- The result is calibrated for the data it was measured on (here, the global test sites). It says nothing about
  Nigeria or another season until it is rechecked there (FRD 8.5).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from .baseline import SCORE_LEVELS, _git_commit, histograms, load_site

METHOD = "platt"
ECE_BINS = 10
ACTIVE_FROM = 0.1                  # ECE over pixels predicted at or above this, so the background bin cannot hide errors
EPS = 0.5 / (SCORE_LEVELS - 1)     # keep logit finite at levels 0 and 255


def level_scores() -> np.ndarray:
    return np.arange(SCORE_LEVELS) / (SCORE_LEVELS - 1)


def _logit(p):
    p = np.clip(p, EPS, 1 - EPS)
    return np.log(p / (1 - p))


def fit_platt(pos: np.ndarray, neg: np.ndarray, iters=100) -> tuple[float, float]:
    """Weighted logistic regression of label on logit(score): p = sigmoid(a * logit(s) + b). Newton's method."""
    x = _logit(level_scores()); w1, w0 = pos.astype(float), neg.astype(float)
    a, b = 1.0, 0.0
    for _ in range(iters):
        p = 1 / (1 + np.exp(-(a * x + b)))
        r = w1 * (1 - p) - w0 * p                      # gradient of the log-likelihood per level
        h = (w1 + w0) * p * (1 - p)
        g = np.array([(r * x).sum(), r.sum()])
        H = np.array([[(h * x * x).sum(), (h * x).sum()], [(h * x).sum(), h.sum()]])
        step = np.linalg.solve(H + 1e-9 * np.eye(2), g)
        a, b = a + step[0], b + step[1]
        if np.abs(step).max() < 1e-10:
            break
    return float(a), float(b)


def platt_table(a: float, b: float) -> np.ndarray:
    return 1 / (1 + np.exp(-(a * _logit(level_scores()) + b)))


def fit_isotonic(pos: np.ndarray, neg: np.ndarray) -> np.ndarray:
    """Pool-adjacent-violators on the 256 levels, weighted by pixel count. Empty levels take their neighbours' value."""
    n = (pos + neg).astype(float)
    blocks = []                                        # [weighted positives, weight, first level, last level]
    for L in range(SCORE_LEVELS):
        if n[L] == 0:
            continue
        blocks.append([float(pos[L]), n[L], L, L])
        while len(blocks) > 1 and blocks[-2][0] / blocks[-2][1] > blocks[-1][0] / blocks[-1][1]:
            p, w, _, e = blocks.pop()
            blocks[-1][0] += p; blocks[-1][1] += w; blocks[-1][3] = e
    out = np.full(SCORE_LEVELS, np.nan)
    for p, w, s, e in blocks:
        out[s:e + 1] = p / w
    idx = np.arange(SCORE_LEVELS); ok = ~np.isnan(out)
    return np.interp(idx, idx[ok], out[ok]) if ok.any() else np.zeros(SCORE_LEVELS)


def reliability(pos: np.ndarray, neg: np.ndarray, table: np.ndarray, bins=ECE_BINS) -> dict:
    """ECE (pixel-weighted, equal-width bins of the predicted value), Brier score and the reliability table."""
    pos = pos.astype(float); neg = neg.astype(float); n = pos + neg; total = n.sum()
    if total == 0:
        return {"pixels": 0, "ece": None, "brier": None, "bins": []}
    which = np.minimum((table * bins).astype(int), bins - 1)
    rows, ece = [], 0.0
    for k in range(bins):
        m = which == k; nk = n[m].sum()
        if nk == 0:
            rows.append({"from": k / bins, "to": (k + 1) / bins, "pixels": 0, "mean_predicted": None, "labelled_fraction": None})
            continue
        pred = float((n[m] * table[m]).sum() / nk); obs = float(pos[m].sum() / nk)
        ece += nk / total * abs(pred - obs)
        rows.append({"from": k / bins, "to": (k + 1) / bins, "pixels": int(nk), "mean_predicted": pred, "labelled_fraction": obs})
    brier = float((pos * (1 - table) ** 2 + neg * table ** 2).sum() / total)
    act = [r for r in rows if r["pixels"] and r["from"] >= ACTIVE_FROM - 1e-9]
    n_act = sum(r["pixels"] for r in act)
    ece_active = (sum(r["pixels"] * abs(r["mean_predicted"] - r["labelled_fraction"]) for r in act) / n_act) if n_act else None
    return {"pixels": int(total), "ece": float(ece), "ece_active": ece_active, "active_pixels": int(n_act), "brier": brier, "base_rate": float(pos.sum() / total), "bins": rows}


def _boot(site_h: list[tuple[np.ndarray, np.ndarray]], tables: dict, n_boot=1000, seed=0) -> dict:
    """95% intervals for ECE and Brier by resampling sites with replacement."""
    rng = np.random.default_rng(seed)
    P = np.stack([p for p, _ in site_h]); N = np.stack([q for _, q in site_h])
    out = {name: {"ece": [], "ece_active": [], "brier": []} for name in tables}
    for _ in range(n_boot):
        i = rng.integers(0, len(site_h), len(site_h))
        p, q = P[i].sum(0), N[i].sum(0)
        for name, t in tables.items():
            r = reliability(p, q, t)
            for k in out[name]:
                out[name][k].append(r[k] if r[k] is not None else np.nan)
    return {name: {m: [float(np.nanpercentile(v, 2.5)), float(np.nanpercentile(v, 97.5))] for m, v in d.items()}
            for name, d in out.items()}


def measure_on_sites(entries, tables: dict, cols=("mine_scale", "region"), n_boot=1000, seed=0) -> dict:
    """Reliability of fixed maps on (row, pos histogram, neg histogram) per site, pooled and per stratum of `cols`."""
    def block(rows):
        P = sum(p for _, p, _ in rows); N = sum(q for _, _, q in rows)
        res = {name: reliability(P, N, tab) for name, tab in tables.items()}
        ci = _boot([(p, q) for _, p, q in rows], tables, n_boot=n_boot, seed=seed) if len(rows) > 1 else {}
        for name in res:
            res[name]["ci95"] = ci.get(name)
        return {"sites": len(rows), **res}
    strata = {"all": entries}
    for col in cols:
        for val in sorted({str(row.get(col)) for row, *_ in entries}):
            strata[f"{col}={val}"] = [x for x in entries if str(x[0].get(col)) == val]
    return {name: block(rs) for name, rs in strata.items()}


def stored_tables(run_dir) -> dict | None:
    """The raw identity and the maps a run's calibration study fitted on global validation, or None if not run."""
    p = Path(run_dir) / "calibration.json"
    if not p.exists():
        return None
    c = json.loads(p.read_text())
    return {"fitted_on": f"{c['run']} {c['fitted_on']}", "applied_method": c["applied_method"],
            "tables": {k: np.asarray(v, float) for k, v in c["tables"].items()}}


def run_calibration(run_dir, prepared_root, labels_root, model="unet", n_boot=1000, seed=0, log=print) -> dict:
    """Fit on validation sites, measure on test sites; writes calibration.json in the run directory."""
    import rasterio
    run_dir = Path(run_dir)
    m = json.loads((run_dir / "metrics.json").read_text())
    r = m["models"][model]
    per_site = {}
    for k in ("val", "test"):
        for i, row in enumerate(r[k]["per_site"], 1):
            sid = row["site_id"]
            _, valid, label, _ = load_site(Path(prepared_root) / sid, Path(labels_root) / sid / "labels.geojson")
            with rasterio.open(run_dir / "scores" / f"{sid}__{model}.tif") as src:
                h = histograms(src.read(1), valid, label)
            per_site[sid] = (k, row, h["pos"], h["neg"])
            log(f"calibration {k} {i}/{len(r[k]['per_site'])} {sid}")
    # The pooled histograms must agree with the stored run, so the calibration describes the scores that were evaluated.
    t = r["threshold_level"]
    for k in ("val", "test"):
        tp = sum(int(p[t:].sum()) for kk, _, p, _ in per_site.values() if kk == k)
        stored = sum(row["tp"] for row in r[k]["per_site"])
        if tp != stored:
            raise ValueError(f"{k}: recomputed true positives {tp} differ from the run record {stored}")

    vp = sum(p for k, _, p, _ in per_site.values() if k == "val"); vn = sum(q for k, _, _, q in per_site.values() if k == "val")
    a, b = fit_platt(vp, vn)
    tables = {"raw": level_scores(), "platt": platt_table(a, b), "isotonic": fit_isotonic(vp, vn)}

    out = {"created_at": datetime.now(timezone.utc).isoformat(), "code_commit": _git_commit(), "model": model,
           "run": run_dir.name, "fitted_on": "validation sites", "measured_on": "test sites",
           "applied_method": METHOD, "platt": {"a": a, "b": b},
           "note": "Valid only for the data measured here; recheck per region and season before calling scores calibrated "
                   "elsewhere (FRD 8.5).",
           "tables": {name: [round(float(v), 6) for v in tab] for name, tab in tables.items()},
           "at_threshold": {"level": t, "raw_score": t / (SCORE_LEVELS - 1),
                            **{name: float(tab[t]) for name, tab in tables.items() if name != "raw"}},
           "val": {}, "test": {}}
    for k in ("val", "test"):
        out[k] = measure_on_sites([v[1:] for v in per_site.values() if v[0] == k], tables, n_boot=n_boot, seed=seed)
        a = out[k]["all"]
        log(f"{k}: ECE raw {a['raw']['ece']:.3f}, platt {a['platt']['ece']:.3f}, isotonic {a['isotonic']['ece']:.3f}; "
            f"ECE >= {ACTIVE_FROM} raw {a['raw']['ece_active']:.3f}, platt {a['platt']['ece_active']:.3f}")
    (run_dir / "calibration.json").write_text(json.dumps(out, indent=1))
    return out
