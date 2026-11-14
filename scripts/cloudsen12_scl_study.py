"""Measure the Sen2Cor scene classification (SCL) cloud classes against CloudSEN12+ expert labels. Changes no mask.

Why: on our mining sites SCL calls bright tailings and pit benches cloud (docs/m1_global_ml_ready_check.md). Our own
scenes are nearly cloud-free, so they cannot say how often SCL is wrong on bright clear ground or set a rule to fix it.
CloudSEN12+ (CC0 1.0, docs/licence_register.csv) has expert cloud labels with each patch's own SCL and ESA WorldCover.

Protocol, fixed before measuring (2026-11-14):
- Patches: label_type "high", 509 x 509 (the 2000 x 2000 patches are left out). Validation split to fit, test split to
  measure once. Pixels with label 99 (no data), SCL 0 or zero reflectance are dropped.
- Truth: expert label 0 clear, 1 thick cloud, 2 thin cloud, 3 shadow. "SCL cloud" is classes 8 and 9, the ones our
  mask drops as cloud (cirrus 10 is reported separately).
- Main measure: share of expert-clear pixels SCL calls cloud, per WorldCover class. Bare / sparse vegetation (60) is
  the analogue of mine ground; built-up (50) and the rest are reported too.
- Option 1 rule: re-admit an SCL-cloud pixel when B02 < t. t is the largest value (step 0.01) for which, on
  validation, at most 1% of the SCL-cloud pixels that experts call thick cloud would be re-admitted. Then on test:
  that leak, and the share of false cloud on clear ground recovered, pooled and on bare ground.
- Intervals: 95% from resampling regions (roi_id) with replacement, 1000 times.
- L2A here is Google Earth Engine's, from 2018-2020 (processing baselines before 04.00, no -1000 offset), so
  reflectance is DN / 10000, matching minefoot.reflectance for those baselines. SCL here comes from those older
  Sen2Cor versions; our Nigerian scenes use baseline 05.xx. Read results as indicative for current SCL.

Runs in its own environment (tacoreader 0.5.3, rasterio, numpy, pandas, requests, aiohttp), not the project's:
  python scripts/cloudsen12_scl_study.py collect WORK_DIR      (reads about 2 GB by HTTP range requests)
  python scripts/cloudsen12_scl_study.py analyse WORK_DIR OUT_JSON
"""
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

LC = [10, 20, 30, 40, 50, 60, 70, 80, 90, 95, 100]       # ESA WorldCover classes
LC_NAME = {10: "tree", 20: "shrub", 30: "grass", 40: "crop", 50: "built-up", 60: "bare / sparse", 70: "snow / ice",
           80: "water", 90: "wetland", 95: "mangrove", 100: "moss / lichen"}
NSCL = 12
NT = 4
BIN = 0.005                                                 # B02 histogram step
NB = 200                                                    # 0 to 1.0
LEAK_MAX = 0.01
SCL_CLOUD = (8, 9)


def _open_datasets():
    import fsspec
    fsspec.config.conf["https"] = {"client_kwargs": {"trust_env": True}}   # go through the session's proxy
    import tacoreader
    return tacoreader.load("tacofoundation:cloudsen12-l2a"), tacoreader.load("tacofoundation:cloudsen12-extra")


def _patch(ds, ex, i, j):
    """Counts for one patch: confusion[lc, scl, target] and B02 histograms of SCL-cloud pixels per target and bare."""
    import rasterio
    s, x = ds.read(i), ex.read(j)
    names = list(x["tortilla:id"])
    with rasterio.open(s.read(0)) as r:
        b02 = r.read(2).astype("float32") / 10000
        gt_l2a = r.transform
    with rasterio.open(s.read(1)) as r:
        t = r.read(1)
    with rasterio.open(x.read(names.index("lc10"))) as r:
        lc = r.read(1)
        assert r.transform == gt_l2a, "WorldCover grid differs from L2A"
    with rasterio.open(x.read(names.index("cloudmask_sen2cor"))) as r:
        scl = r.read(1)
        assert r.transform == gt_l2a, "SCL grid differs from L2A"
    ok = (t != 99) & (scl > 0) & (b02 > 0) & (t < NT) & (scl < NSCL)
    li = np.searchsorted(LC, lc)                              # index into LC; unknown classes dropped
    ok &= (li < len(LC)) & (np.asarray(LC)[np.minimum(li, len(LC) - 1)] == lc)
    conf = np.zeros((len(LC), NSCL, NT), "int64")
    np.add.at(conf, (li[ok], scl[ok].astype(int), t[ok].astype(int)), 1)
    cl = ok & np.isin(scl, SCL_CLOUD)
    hb = np.minimum((b02 / BIN).astype(int), NB - 1)
    bare = lc == 60
    hist = np.zeros((2, NT, NB), "int64")                     # [not bare / bare, target, B02 bin]
    np.add.at(hist, (bare[cl].astype(int), t[cl].astype(int), hb[cl]), 1)
    return conf, hist


def collect(work):
    import pandas as pd
    work = Path(work); work.mkdir(parents=True, exist_ok=True)
    ds, ex = _open_datasets()
    m, e = pd.DataFrame(ds), pd.DataFrame(ex)
    pick = m[(m.label_type == "high") & (m.real_proj_shape == 509) & m["tortilla:data_split"].isin(["validation", "test"])]
    eidx = {tid: k for k, tid in zip(range(len(e)), e["tortilla:id"])}
    for split in ("validation", "test"):
        out = work / f"{split}.npz"
        if out.exists():
            print(f"{split}: already collected"); continue
        rows = pick[pick["tortilla:data_split"] == split]
        pos = {lab: k for k, lab in enumerate(m.index)}
        jobs = [(pos[i], eidx[m.loc[i, "tortilla:id"]], m.loc[i, "tortilla:id"], m.loc[i, "roi_id"]) for i in rows.index]
        confs, hists, ids, rois, failed = [], [], [], [], []

        def run(job):
            try:
                return job, _patch(ds, ex, job[0], job[1])
            except Exception as err:                       # recorded, never dropped silently
                return job, err
        with ThreadPoolExecutor(4) as pool:
            for n, (job, res) in enumerate(pool.map(run, jobs), 1):
                if isinstance(res, Exception):
                    failed.append([job[2], repr(res)[:200]])
                else:
                    confs.append(res[0]); hists.append(res[1]); ids.append(job[2]); rois.append(job[3])
                if n % 50 == 0:
                    print(f"{split} {n}/{len(jobs)} failed {len(failed)}", flush=True)
        np.savez_compressed(out, conf=np.stack(confs), hist=np.stack(hists), ids=np.array(ids), rois=np.array(rois))
        (work / f"{split}_failed.json").write_text(json.dumps(failed, indent=1))
        print(f"{split}: {len(ids)} patches, {len(failed)} failed")


def _rates(conf, hist, t_rule):
    """Measures on pooled counts. conf[lc, scl, target], hist[bare, target, bin]."""
    clear = conf[:, :, 0]; thick = conf[:, :, 1]; thin = conf[:, :, 2]
    is_cloud = np.zeros(NSCL, bool); is_cloud[list(SCL_CLOUD)] = True
    out = {}
    tot_clear = clear.sum()
    out["false_cloud_on_clear"] = float(clear[:, is_cloud].sum() / tot_clear) if tot_clear else None
    out["cirrus_on_clear"] = float(clear[:, 10].sum() / tot_clear) if tot_clear else None
    out["thick_caught"] = float(thick[:, is_cloud].sum() / thick.sum()) if thick.sum() else None
    out["thick_or_thin_caught_incl_cirrus"] = float((thick + thin)[:, list(SCL_CLOUD) + [10]].sum() / (thick + thin).sum()) \
        if (thick + thin).sum() else None
    sc = conf[:, is_cloud, :].sum(axis=(0, 1))
    out["scl_cloud_that_is_clear"] = float(sc[0] / sc.sum()) if sc.sum() else None
    out["per_landcover"] = {}
    for k, c in enumerate(LC):
        n = clear[k].sum()
        out["per_landcover"][LC_NAME[c]] = {
            "clear_pixels": int(n),
            "false_cloud_on_clear": float(clear[k, is_cloud].sum() / n) if n else None,
            "scl_bare_on_clear": float(clear[k, 5].sum() / n) if n else None}
    if t_rule is not None:
        cut = int(round(t_rule / BIN))
        h = hist.sum(0)                                       # all land cover
        out["rule"] = {"B02_below": t_rule,
                       "thick_cloud_readmitted": float(h[1, :cut].sum() / h[1].sum()) if h[1].sum() else None,
                       "thin_cloud_readmitted": float(h[2, :cut].sum() / h[2].sum()) if h[2].sum() else None,
                       "false_cloud_recovered": float(h[0, :cut].sum() / h[0].sum()) if h[0].sum() else None,
                       "false_cloud_recovered_bare": float(hist[1, 0, :cut].sum() / hist[1, 0].sum()) if hist[1, 0].sum() else None}
    return out


def _flat(d, prefix=""):
    for k, v in d.items():
        if isinstance(v, dict):
            yield from _flat(v, f"{prefix}{k}.")
        elif isinstance(v, (int, float)) or v is None:
            yield f"{prefix}{k}", v


def _boot(conf, hist, rois, t_rule, n=1000, seed=0):
    rng = np.random.default_rng(seed)
    uniq = np.unique(rois); groups = [np.flatnonzero(rois == r) for r in uniq]
    vals = {}
    for _ in range(n):
        idx = np.concatenate([groups[g] for g in rng.integers(0, len(groups), len(groups))])
        for k, v in _flat(_rates(conf[idx].sum(0), hist[idx].sum(0), t_rule)):
            if k.endswith("clear_pixels") or k.endswith("B02_below"):
                continue
            vals.setdefault(k, []).append(np.nan if v is None else v)
    return {k: [float(np.nanpercentile(v, 2.5)), float(np.nanpercentile(v, 97.5))] for k, v in vals.items()}


def fit_rule(hist):
    """Largest B02 cut (0.01 steps) re-admitting at most LEAK_MAX of SCL-cloud pixels experts call thick cloud."""
    h = hist.sum(axis=(0, 1))[1]                              # per-patch [patch, bare, target, bin] -> thick cloud bins
    best = None
    for t in np.arange(0.01, 1.0001, 0.01):
        cut = int(round(t / BIN))
        if h.sum() and h[:cut].sum() / h.sum() <= LEAK_MAX:
            best = round(float(t), 2)
    return best


def analyse(work, out):
    work = Path(work)
    v, te = np.load(work / "validation.npz"), np.load(work / "test.npz")
    t_rule = fit_rule(v["hist"])
    res = {"protocol": __doc__.split("Protocol, fixed before measuring (2026-11-14):")[1].split("Runs in")[0].strip(),
           "dataset": "CloudSEN12+ v1.1.2 (CC0 1.0), tacofoundation/cloudsen12, label_type high, 509 px patches",
           "rule_fitted_on": "validation", "rule": {"B02_below": t_rule, "leak_max_on_validation": LEAK_MAX}}
    for name, d in (("validation", v), ("test", te)):
        res[name] = {"patches": int(len(d["ids"])), "regions": int(len(np.unique(d["rois"]))),
                     "pooled": _rates(d["conf"].sum(0), d["hist"].sum(0), t_rule),
                     "ci95": _boot(d["conf"], d["hist"], d["rois"], t_rule),
                     "failed": json.loads((work / f"{name}_failed.json").read_text())}
    Path(out).write_text(json.dumps(res, indent=1))
    p, ci = res["test"]["pooled"], res["test"]["ci95"]
    print(f"rule fitted on validation: B02 < {t_rule}")
    for k in ("false_cloud_on_clear", "thick_caught", "scl_cloud_that_is_clear"):
        print(f"test {k}: {p[k]:.4f} {ci[k]}")
    for lc, r in p["per_landcover"].items():
        if r["clear_pixels"]:
            print(f"  {lc:14s} clear px {r['clear_pixels']:>11,}  false cloud {r['false_cloud_on_clear']:.4f} "
                  f"{ci.get(f'per_landcover.{lc}.false_cloud_on_clear')}")
    if "rule" in p:
        for k, val in p["rule"].items():
            print(f"test rule {k}: {val} {ci.get(f'rule.{k}', '')}")


if __name__ == "__main__":
    {"collect": collect, "analyse": analyse}[sys.argv[1]](*sys.argv[2:])
