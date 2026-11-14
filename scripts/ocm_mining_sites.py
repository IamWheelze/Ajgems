"""OmniCloudMask against SCL on our own mining sites: how much labelled mine ground each calls cloud. Changes no mask.

Same 40 Global ML-ready sites as scripts/cloud_mask_study.py (their ids are in docs/evidence/cloud_mask_study_40.json).
Each site is fetched (same scene, Planetary Computer), measured and its raw file deleted, so the disk is not filled.
Reflectance comes from the preprocessing path's `to_reflectance`, unmasked. OmniCloudMask 1.8.1, model 4.0, inputs
B04, B03, B8A, as in scripts/cloudsen12_ocm_study.py.

There are no cloud labels here. The scenes are nearly cloud-free (median scene cloud 0.49%), so cloud called on
labelled mine ground is mostly false; "far" (more than 1 km from any label) is reported for comparison, not as truth.

Usage (environment with omnicloudmask and the project): python scripts/ocm_mining_sites.py WORK_DIR OUT_JSON
"""
import json
import shutil
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from minefoot.labels import rasterize_to, read_vectors  # noqa: E402
from minefoot.reflectance import Radiometry, to_reflectance  # noqa: E402

FAR_PX = 100
MASKS = ("scl_cloud", "ocm_thick", "ocm_thick_or_thin")


def main(work, out):
    import rasterio
    import torch
    from scipy.ndimage import distance_transform_edt
    from minefoot.datasets import fetch_sites
    from omnicloudmask import predict_from_array
    torch.set_num_threads(2)
    work = Path(work); work.mkdir(parents=True, exist_ok=True)
    root = Path(__file__).resolve().parents[1]
    ids = [r["site"] for r in json.loads((root / "docs/evidence/cloud_mask_study_40.json").read_text())["per_site"]]
    rows_path = work / "ocm_sites_rows.json"
    rows = json.loads(rows_path.read_text()) if rows_path.exists() else []
    done = {r["site"] for r in rows}
    for n, sid in enumerate(ids, 1):
        if sid in done:
            continue
        rep = fetch_sites(root / "data/datasets/global_ml_ready/sites.gpkg", work, site_ids=[sid], provider="planetary")
        d = work / sid
        if rep["failed"]:
            rows.append({"site": sid, "failed": str(rep["failed"])[:300]}); continue
        meta = json.loads((d / "scene.json").read_text())
        with rasterio.open(d / "raw_dn.tif") as s:
            names = list(s.descriptions)
            band = lambda b: to_reflectance(s.read(names.index(b) + 1), Radiometry(**meta["radiometry"][b]))
            rgn = np.stack([band("B04"), band("B03"), band("B8A")])
            scl = s.read(names.index("SCL") + 1)
        label = rasterize_to(d / "raw_dn.tif", read_vectors(d / "labels.geojson")).astype(bool)
        valid = np.isfinite(rgn).all(0) & (scl > 0)
        ocm = predict_from_array(np.nan_to_num(rgn, nan=0.0), patch_size=1000, patch_overlap=300, inference_device="cpu",
                                 model_version=4.0, destination_model_dir=root / "data/weights/omnicloudmask",
                                 no_data_value=0)[0]
        far = distance_transform_edt(~label) > FAR_PX
        m = {"scl_cloud": np.isin(scl, (8, 9)), "ocm_thick": ocm == 1, "ocm_thick_or_thin": np.isin(ocm, (1, 2))}
        r = {"site": sid, "cloud_pct_scene": meta.get("cloud_pct_scene"),
             "label_px": int((label & valid).sum()), "far_px": int((far & valid).sum())}
        for k, mk in m.items():
            r[f"{k}_on_mines"] = int((mk & label & valid).sum()); r[f"{k}_far"] = int((mk & far & valid).sum())
        r["scl_cloud_on_mines_ocm_clear"] = int((m["scl_cloud"] & label & valid & (ocm == 0)).sum())
        rows.append(r); rows_path.write_text(json.dumps(rows, indent=1))
        shutil.rmtree(d)                                                 # raw file no longer needed
        print(f"{n}/{len(ids)} {sid} on mines: SCL {r['scl_cloud_on_mines']} OCM {r['ocm_thick_or_thin_on_mines']} "
              f"of {r['label_px']}", flush=True)
    ok = [r for r in rows if "failed" not in r]
    pooled = {"sites": len(ok), "failed": [r for r in rows if "failed" in r]}
    for k in MASKS:
        pooled[f"{k}_on_mines"] = sum(r[f"{k}_on_mines"] for r in ok) / sum(r["label_px"] for r in ok)
        pooled[f"{k}_far"] = sum(r[f"{k}_far"] for r in ok) / sum(r["far_px"] for r in ok)
    s = sum(r["scl_cloud_on_mines"] for r in ok)
    pooled["scl_cloud_on_mines_that_ocm_calls_clear"] = sum(r["scl_cloud_on_mines_ocm_clear"] for r in ok) / s if s else None
    for k in MASKS:
        pooled[f"sites_over_5pct_on_mines_{k}"] = sum(1 for r in ok if r["label_px"] and r[f"{k}_on_mines"] / r["label_px"] > 0.05)
    Path(out).write_text(json.dumps({"what": __doc__.split("Usage")[0].strip(), "pooled": pooled, "per_site": rows}, indent=1))
    print(json.dumps(pooled, indent=1))


if __name__ == "__main__":
    main(*sys.argv[1:3])
