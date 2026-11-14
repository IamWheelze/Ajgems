"""OmniCloudMask (option 2) against CloudSEN12+ expert labels, on the same test pixels as SCL. Changes no mask.

Protocol, fixed before measuring (2026-11-14):
- Test patches as in scripts/cloudsen12_scl_study.py (label_type "high", 509 px, test split, 975 patches). Nothing is
  fitted, so validation is not used.
- OmniCloudMask 1.8.1, model version 4.0 (its default ensemble), inputs B04, B03, B8A (its Sentinel-2 default), L2A
  reflectance, no-data value 0. Its output classes follow CloudSEN12: 0 clear, 1 thick, 2 thin, 3 shadow.
- Training overlap: its training-data map (model version 4) counts exactly twice the CloudSEN12 "high" training-split
  patches per tile in all 2,158 tiles (L1C plus L2A), so the test split was not trained on. Other training sets
  (scribble, 2k, KappaSet, its own hard negatives and scribbles) touch 243 test patches' tiles. Results are given for
  all 975 patches and for the 732 in tiles with no OmniCloudMask training data at all ("clean").
- Compared masks on the same pixels: SCL classes 8, 9 (what our mask drops); OmniCloudMask thick (1); OmniCloudMask
  thick or thin (1, 2).
- Measures: share of expert-clear pixels called cloud, pooled and per WorldCover class; share of expert thick and thin
  cloud caught. Paired differences against SCL with 95% intervals from resampling regions (roi_id), 1000 times.

Runs in an environment with omnicloudmask, tacoreader and the project's torch:
  python scripts/cloudsen12_ocm_study.py collect WORK_DIR CLEAN_IDS_CSV
  python scripts/cloudsen12_ocm_study.py analyse WORK_DIR OUT_JSON
"""
import hashlib
import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cloudsen12_scl_study import LC, LC_NAME, NSCL, NT, _open_datasets  # noqa: E402

MODEL_VERSION = 4.0
MASKS = {"scl_cloud": ("scl", (8, 9)), "ocm_thick": ("ocm", (1,)), "ocm_thick_or_thin": ("ocm", (1, 2))}


def _patch(ds, ex, i, j, predict, weights_dir):
    import rasterio
    s, x = ds.read(i), ex.read(j)
    names = list(x["tortilla:id"])
    with rasterio.open(s.read(0)) as r:
        rgn = r.read([4, 3, 9]).astype("float32") / 10000          # B04, B03, B8A (1-based band indices)
        gt = r.transform
    with rasterio.open(s.read(1)) as r:
        t = r.read(1)
    with rasterio.open(x.read(names.index("lc10"))) as r:
        lc = r.read(1); assert r.transform == gt
    with rasterio.open(x.read(names.index("cloudmask_sen2cor"))) as r:
        scl = r.read(1); assert r.transform == gt
    ocm = predict(rgn, patch_size=512, patch_overlap=0, inference_device="cpu", model_version=MODEL_VERSION,
                  destination_model_dir=weights_dir, no_data_value=0)[0]
    ok = (t != 99) & (t < NT) & (scl > 0) & (scl < NSCL) & (rgn > 0).all(0) & (ocm < NT)
    li = np.searchsorted(LC, lc)
    ok &= (li < len(LC)) & (np.asarray(LC)[np.minimum(li, len(LC) - 1)] == lc)
    conf = np.zeros((len(LC), NSCL, NT, NT), "int64")               # [lc, scl, ocm, target]
    np.add.at(conf, (li[ok], scl[ok].astype(int), ocm[ok].astype(int), t[ok].astype(int)), 1)
    return conf


def collect(work, clean_csv):
    import pandas as pd
    import torch
    from omnicloudmask import predict_from_array
    torch.set_num_threads(2)
    work = Path(work); work.mkdir(parents=True, exist_ok=True)
    weights = Path("data/weights/omnicloudmask"); weights.mkdir(parents=True, exist_ok=True)
    out = work / "ocm_test.npz"
    ds, ex = _open_datasets()
    m, e = pd.DataFrame(ds), pd.DataFrame(ex)
    rows = m[(m.label_type == "high") & (m.real_proj_shape == 509) & (m["tortilla:data_split"] == "test")]
    clean = set(pd.read_csv(clean_csv).iloc[:, 0])
    pos = {lab: k for k, lab in enumerate(m.index)}
    eidx = {tid: k for k, tid in enumerate(e["tortilla:id"])}
    done = {}
    part = work / "ocm_partial.npz"
    if part.exists():                                              # resume after an interruption
        p = np.load(part, allow_pickle=True); done = dict(zip(p["ids"], p["conf"]))
    failed, t0 = [], time.time()
    for n, i in enumerate(rows.index, 1):
        tid = m.loc[i, "tortilla:id"]
        if tid in done:
            continue
        try:
            done[tid] = _patch(ds, ex, pos[i], eidx[tid], predict_from_array, weights)
        except Exception as err:                                   # recorded, never dropped silently
            failed.append([tid, repr(err)[:200]])
        if n % 25 == 0:
            ids = list(done); np.savez_compressed(part, ids=np.array(ids), conf=np.stack([done[k] for k in ids]))
            print(f"{n}/{len(rows)} failed {len(failed)} {(time.time() - t0) / max(n, 1):.1f}s/patch", flush=True)
    ids = [tid for tid in rows["tortilla:id"] if tid in done]
    roi = dict(zip(rows["tortilla:id"], rows["roi_id"]))
    np.savez_compressed(out, ids=np.array(ids), rois=np.array([roi[k] for k in ids]),
                        clean=np.array([k in clean for k in ids]), conf=np.stack([done[k] for k in ids]))
    (work / "ocm_failed.json").write_text(json.dumps(failed, indent=1))
    files = sorted(weights.glob("*.safetensors")) + sorted(weights.glob("*.pth"))
    (work / "ocm_weights.json").write_text(json.dumps(
        {f.name: hashlib.sha256(f.read_bytes()).hexdigest() for f in files}, indent=1))
    print(f"test: {len(ids)} patches, {len(failed)} failed")


def _measures(conf):
    """conf[lc, scl, ocm, target] pooled -> per mask: false cloud on clear (pooled and per land cover), thick/thin caught."""
    out = {}
    for name, (src, cls) in MASKS.items():
        if src == "scl":
            c = conf[:, list(cls), :, :].sum(axis=(1, 2)); a = conf.sum(axis=(1, 2))        # [lc, target]
        else:
            c = conf[:, :, list(cls), :].sum(axis=(1, 2)); a = conf.sum(axis=(1, 2))
        r = {"false_cloud_on_clear": float(c[:, 0].sum() / a[:, 0].sum()),
             "thick_caught": float(c[:, 1].sum() / a[:, 1].sum()),
             "thin_caught": float(c[:, 2].sum() / a[:, 2].sum())}
        for k, lc in enumerate(LC):
            if a[k, 0]:
                r[f"false_cloud_on_clear.{LC_NAME[lc]}"] = float(c[k, 0] / a[k, 0])
        out[name] = r
    return out


def _block(conf, rois, n_boot=1000, seed=0):
    pooled = _measures(conf.sum(0))
    rng = np.random.default_rng(seed)
    uniq = np.unique(rois); groups = [np.flatnonzero(rois == r) for r in uniq]
    draws = []
    for _ in range(n_boot):
        idx = np.concatenate([groups[g] for g in rng.integers(0, len(groups), len(groups))])
        draws.append(_measures(conf[idx].sum(0)))
    ci, diff = {}, {}
    for name in MASKS:
        ci[name] = {k: [float(np.nanpercentile([d[name].get(k, np.nan) for d in draws], q)) for q in (2.5, 97.5)]
                    for k in pooled[name]}
        if name != "scl_cloud":
            diff[f"{name}_minus_scl"] = {
                k: {"point": pooled[name][k] - pooled["scl_cloud"][k],
                    "ci95": [float(np.nanpercentile([d[name].get(k, np.nan) - d["scl_cloud"].get(k, np.nan)
                                                     for d in draws], q)) for q in (2.5, 97.5)]}
                for k in pooled[name] if k in pooled["scl_cloud"]}
    return {"patches": int(len(rois)), "regions": int(len(uniq)), "pooled": pooled, "ci95": ci, "paired": diff}


def analyse(work, out):
    work = Path(work)
    d = np.load(work / "ocm_test.npz")
    res = {"protocol": __doc__.split("Protocol, fixed before measuring (2026-11-14):")[1].split("Runs in")[0].strip(),
           "model": f"OmniCloudMask 1.8.1, model version {MODEL_VERSION}",
           "weights_sha256": json.loads((work / "ocm_weights.json").read_text()),
           "failed": json.loads((work / "ocm_failed.json").read_text()),
           "all": _block(d["conf"], d["rois"]),
           "clean": _block(d["conf"][d["clean"]], d["rois"][d["clean"]])}
    Path(out).write_text(json.dumps(res, indent=1))
    for sub in ("all", "clean"):
        b = res[sub]; print(f"--- {sub}: {b['patches']} patches, {b['regions']} regions")
        for name in MASKS:
            p, c = b["pooled"][name], b["ci95"][name]
            print(f"{name:18s} " + "  ".join(f"{k} {p[k]:.4f} [{c[k][0]:.4f}, {c[k][1]:.4f}]" for k in
                                             ("false_cloud_on_clear", "false_cloud_on_clear.bare / sparse",
                                              "thick_caught", "thin_caught") if k in p))
        for name, dd in b["paired"].items():
            print(f"{name:26s} " + "  ".join(f"{k} {v['point']:+.4f} [{v['ci95'][0]:+.4f}, {v['ci95'][1]:+.4f}]"
                                             for k, v in dd.items() if k in ("false_cloud_on_clear",
                                             "false_cloud_on_clear.bare / sparse", "thick_caught")))


if __name__ == "__main__":
    {"collect": collect, "analyse": analyse}[sys.argv[1]](*sys.argv[2:])
