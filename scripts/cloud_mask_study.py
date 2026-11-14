"""Evidence for the open cloud-mask question (docs/m1_global_ml_ready_check.md, option 1). Changes no mask.

Question: among pixels SCL calls cloud (classes 8 and 9), does a simple reflectance rule separate the bright mine
ground that SCL mislabels from cloud?

There is no labelled cloud here, so two proxy groups stand in, and both are impure:
- "on mines": SCL cloud inside the mining labels. Visual checks showed these follow tailings and pit benches.
- "far": SCL cloud more than 1 km from any label. Mostly real cloud, but bright bare ground away from mines is in it too.
A rule is promising only if it re-admits most "on mines" pixels and few "far" ones. A threshold for use still needs
labelled cloud (option 1 in the findings doc says so); this script only shows whether that test is worth doing.

Prepared reflectance has these pixels blanked (NaN), so the script reads the raw download (raw_dn.tif and scene.json
from `minefoot fetch-sites`) and converts it with the preprocessing path's own `to_reflectance`, without the mask.

Usage: python scripts/cloud_mask_study.py RAW_ROOT LABELS_ROOT OUT_JSON
"""
import json
import sys
from pathlib import Path

import numpy as np
import rasterio
from scipy.ndimage import distance_transform_edt

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from minefoot.labels import rasterize_to, read_vectors  # noqa: E402
from minefoot.reflectance import Radiometry, to_reflectance  # noqa: E402
from minefoot.schema import BANDS  # noqa: E402

FAR_PX = 100                          # 1 km at 10 m
CLOUD = (8, 9)
B = {b: i for i, b in enumerate(BANDS)}
BLUE_T = [0.15, 0.20, 0.25, 0.30, 0.35, 0.40]
RATIO_T = [0.6, 0.8, 1.0]             # B02 / B11: soil and tailings are redder (low), cloud is flat or blue (high)
PER_GROUP = 20000                     # sampled pixels kept per site and group, so a few big sites cannot dominate


def main(raw_root, labels_root, out):
    raw_root, labels_root = Path(raw_root), Path(labels_root)
    rng = np.random.default_rng(0)
    groups = {"on_mines": [], "far": [], "bare_on_mines": []}
    site_rows = []
    for d in sorted(p for p in raw_root.iterdir() if (p / "raw_dn.tif").exists()):
        lab = labels_root / d.name / "labels.geojson"
        if not lab.exists():
            continue
        meta = json.loads((d / "scene.json").read_text())
        with rasterio.open(d / "raw_dn.tif") as s:
            names = list(s.descriptions)
            refl = np.stack([to_reflectance(s.read(names.index(b) + 1), Radiometry(**meta["radiometry"][b]))
                             for b in ("B02", "B04", "B08", "B11")])
            scl = s.read(names.index("SCL") + 1)
        label = rasterize_to(d / "raw_dn.tif", read_vectors(lab)).astype(bool)
        if scl.shape != label.shape:
            raise ValueError(f"{d.name}: SCL grid {scl.shape} differs from reflectance {label.shape}")
        far = distance_transform_edt(~label) > FAR_PX
        cloud = np.isin(scl, CLOUD) & np.isfinite(refl).all(0)
        masks = {"on_mines": cloud & label, "far": cloud & far, "bare_on_mines": (scl == 5) & label}
        row = {"site": d.name, **{k: int(m.sum()) for k, m in masks.items()}, "label_px": int(label.sum())}
        site_rows.append(row)
        for k, m in masks.items():
            idx = np.flatnonzero(m)
            if len(idx) > PER_GROUP:
                idx = rng.choice(idx, PER_GROUP, replace=False)
            groups[k].append(refl.reshape(4, -1)[:, idx])
    data = {k: np.concatenate(v, axis=1) if v else np.zeros((4, 0)) for k, v in groups.items()}
    sites_with = {k: sum(1 for r in site_rows if r[k] > 0) for k in groups}

    def share(x, rule):
        return float(rule(x).mean()) if x.shape[1] else None

    rules = {}
    for t in BLUE_T:
        rules[f"B02 < {t}"] = lambda x, t=t: x[0] < t
    for t in RATIO_T:
        rules[f"B02/B11 < {t}"] = lambda x, t=t: x[0] / np.maximum(x[3], 1e-4) < t
    for t in BLUE_T[1:4]:
        rules[f"B02 < {t} and B02/B11 < 0.8"] = lambda x, t=t: (x[0] < t) & (x[0] / np.maximum(x[3], 1e-4) < 0.8)

    pct = lambda x: {f"p{q}": float(np.percentile(x, q)) for q in (5, 25, 50, 75, 95)} if len(x) else None
    res = {
        "what": "Share of SCL-cloud pixels (classes 8, 9) each candidate rule would re-admit as 'suspect bright'. Proxy "
                "groups, no labelled cloud: on_mines is mostly mislabelled tailings, far (>1 km from labels) is mostly "
                "real cloud but impure. A good rule is high on on_mines and low on far.",
        "sites": len(site_rows), "sites_with_pixels": sites_with,
        "sampled_pixels": {k: int(v.shape[1]) for k, v in data.items()}, "per_site_cap": PER_GROUP,
        "distributions": {k: {"B02": pct(v[0]), "B11": pct(v[3]),
                              "B02/B11": pct(v[0] / np.maximum(v[3], 1e-4))} for k, v in data.items()},
        "rules": {name: {k: share(v, r) for k, v in data.items()} for name, r in rules.items()},
        "per_site": site_rows,
    }
    Path(out).write_text(json.dumps(res, indent=1))
    print(f"{len(site_rows)} sites; sites with pixels {sites_with}; sampled {res['sampled_pixels']}")
    for name, r in res["rules"].items():
        print(f"{name:32s} " + "  ".join(f"{k} {v:.3f}" if v is not None else f"{k} -" for k, v in r.items()))


if __name__ == "__main__":
    main(*sys.argv[1:4])
