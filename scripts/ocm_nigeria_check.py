"""OmniCloudMask against SCL on current-baseline Nigerian scenes, before any switch of the cloud mask. Changes no mask.

Founder decision 2026-11-14: adopt OmniCloudMask (option 2) after a Nigerian check. This is that check.

Protocol, fixed before measuring (2026-11-14):
- Six 20 km windows in Nigeria, each at least 50 km from both labelling regions in configs/nigeria_aois.yaml (the
  split's block size), so the locked-test regions are not looked at. No mining model is run and no mine label is used.
- Window types: three dry-season clear windows (Kano: dry farmland and bare ground; Maiduguri: sand; Lake Kainji:
  open water) and three cloudy windows (Abuja, Niger Delta, Lagos) for real cloud on current scenes.
- Scenes: Planetary Computer, current processing baselines (offset -1000 DN, read from the scene, converted with the
  preprocessing path's own `to_reflectance`). Clear windows: the clearest scene in the season. Cloudy windows: scenes
  ordered by distance of tile cloud cover from 30%; the first whose window has at least 5% SCL cloud (8, 9) is used.
  Scene IDs are pinned in the output.
- OmniCloudMask 1.8.1, model 4.0, inputs B04, B03, B8A, patch 1000, overlap 300, as in the other studies.
- There are no cloud labels here, so the measures are agreement and a visual check, not accuracy:
  share of SCL bare (5) and SCL water (6) pixels OmniCloudMask calls cloud (thick, and thick or thin); share of SCL
  thick cloud (9) and of all SCL cloud (8, 9) it calls cloud; the full SCL x OmniCloudMask table; panels per window.
- Pass marks, provisional because unlabelled: on the clear windows OmniCloudMask thick calls at most 1% of SCL-bare
  pixels cloud, and at most 2% of SCL-water pixels; on the cloudy windows it calls cloud at least 80% of SCL thick
  cloud (9). Disagreements are inspected in the panels before anything is concluded.

Usage (environment with omnicloudmask and the project):
  python scripts/ocm_nigeria_check.py run WORK_DIR OUT_JSON PANEL_DIR
"""
import json
import shutil
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from minefoot.reflectance import Radiometry, to_reflectance  # noqa: E402

HALF = 0.09                                     # about 20 km boxes
MIN_KM_FROM_AOI = 50
WINDOWS = [
    {"name": "kano_dry", "kind": "clear", "lon": 8.52, "lat": 12.00, "start": "2025-12-01", "end": "2026-02-28"},
    {"name": "maiduguri_sand", "kind": "clear", "lon": 13.15, "lat": 11.85, "start": "2025-12-01", "end": "2026-02-28"},
    {"name": "kainji_water", "kind": "clear", "lon": 4.55, "lat": 10.35, "start": "2025-12-01", "end": "2026-02-28"},
    {"name": "abuja_cloudy", "kind": "cloudy", "lon": 7.45, "lat": 9.05, "start": "2026-04-01", "end": "2026-10-31"},
    {"name": "delta_cloudy", "kind": "cloudy", "lon": 6.95, "lat": 4.85, "start": "2025-11-01", "end": "2026-10-31"},
    {"name": "lagos_cloudy", "kind": "cloudy", "lon": 3.45, "lat": 6.50, "start": "2025-11-01", "end": "2026-10-31"},
]
SCL_NAME = {0: "no data", 1: "saturated", 2: "dark", 3: "shadow", 4: "vegetation", 5: "bare", 6: "water",
            7: "unclassified", 8: "cloud medium", 9: "cloud high", 10: "cirrus", 11: "snow"}


def _km_to_box(lon, lat, box):
    w, s, e, n = box
    dx = max(w - lon, 0, lon - e) * 111.32 * np.cos(np.radians(lat)); dy = max(s - lat, 0, lat - n) * 110.57
    return float(np.hypot(dx, dy)) - HALF * 111                      # conservative: window edge, not centre


def _aoi_boxes():
    import yaml
    return {r["name"]: r["bbox"] for r in yaml.safe_load((ROOT / "configs/nigeria_aois.yaml").read_text())["regions"]}


def _read(d):
    import rasterio
    meta = json.loads((d / "scene.json").read_text())
    with rasterio.open(d / "raw_dn.tif") as s:
        names = list(s.descriptions)
        band = lambda b: to_reflectance(s.read(names.index(b) + 1), Radiometry(**meta["radiometry"][b]))
        rgn = np.stack([band("B04"), band("B03"), band("B8A")])
        scl = s.read(names.index("SCL") + 1)
    return meta, rgn, scl


def _panel(rgn, scl, ocm, path):
    from PIL import Image
    rgb = np.clip(np.nan_to_num(rgn[[0, 1, 0]]).transpose(1, 2, 0) / 0.3, 0, 1)
    rgb[..., 2] = np.clip(np.nan_to_num(rgn[1]) / 0.3, 0, 1)        # simple R, G, G display of the model inputs
    tc = (rgb * 255).astype("uint8")
    def over(mask, colour):
        o = tc.copy(); o[mask] = (0.45 * o[mask] + 0.55 * np.array(colour)).astype("uint8"); return o
    s_cloud, o_cloud = np.isin(scl, (8, 9, 10)), np.isin(ocm, (1, 2))
    dis = np.zeros_like(tc); dis[s_cloud & ~o_cloud] = (0, 255, 255); dis[o_cloud & ~s_cloud] = (255, 0, 255)
    dis[s_cloud & o_cloud] = (255, 255, 255)
    tiles = [tc, over(s_cloud, (0, 255, 255)), over(o_cloud, (255, 0, 255)), dis]
    im = Image.fromarray(np.concatenate(tiles, axis=1))
    im.thumbnail((2400, 600)); im.save(path, optimize=True)


def run(work, out, panels):
    import torch
    from minefoot import catalog
    from omnicloudmask import predict_from_array
    torch.set_num_threads(1)
    work, panels = Path(work), Path(panels); work.mkdir(parents=True, exist_ok=True); panels.mkdir(parents=True, exist_ok=True)
    boxes = _aoi_boxes(); rows = []
    for w in WINDOWS:
        dist = {k: round(_km_to_box(w["lon"], w["lat"], b), 1) for k, b in boxes.items()}
        assert min(dist.values()) >= MIN_KM_FROM_AOI, (w["name"], dist)
        bbox = (w["lon"] - HALF, w["lat"] - HALF, w["lon"] + HALF, w["lat"] + HALF)
        _, items = catalog.search(bbox, w["start"], w["end"], max_cloud=100 if w["kind"] == "cloudy" else 10,
                                  providers=["planetary"])
        if w["kind"] == "cloudy":
            items = sorted(items, key=lambda i: (abs(i.properties.get("eo:cloud_cover", 100) - 30), i.id))
        tried = []
        for item in items[:6]:
            d = catalog.fetch_scene(item, "planetary", bbox, work, name=w["name"])
            meta, rgn, scl = _read(d)
            win_cloud = float(np.isin(scl, (8, 9)).mean())
            tried.append({"scene_id": item.id, "window_scl_cloud": round(win_cloud, 4)})
            if w["kind"] == "clear" or win_cloud >= 0.05:
                break
            shutil.rmtree(d)
        else:
            rows.append({**w, "failed": "no scene met the window rule", "tried": tried}); continue
        ocm = predict_from_array(np.nan_to_num(rgn, nan=0.0), patch_size=1000, patch_overlap=300, inference_device="cpu",
                                 model_version=4.0, destination_model_dir=ROOT / "data/weights/omnicloudmask",
                                 no_data_value=0)[0]
        valid = np.isfinite(rgn).all(0) & (scl > 0)
        table = np.zeros((12, 4), "int64")
        np.add.at(table, (scl[valid].astype(int), ocm[valid].astype(int)), 1)
        share = lambda scl_cls, ocm_cls: (float(table[list(scl_cls)][:, list(ocm_cls)].sum() / table[list(scl_cls)].sum())
                                          if table[list(scl_cls)].sum() else None)
        r = {**w, "km_from_labelling_regions": dist, "scene_id": meta["scene_id"], "datetime": meta["datetime"],
             "processing_baseline": meta["processing_baseline"],
             "offset_dn": {b: meta["radiometry"][b]["offset_dn"] for b in ("B04", "B03", "B8A")},
             "tried": tried, "valid_px": int(valid.sum()),
             "min_reflectance_inputs": float(np.nanmin(rgn)),
             "scl_share": {SCL_NAME[k]: float(table[k].sum() / table.sum()) for k in range(12) if table[k].sum()},
             "ocm_share": {n: float(table[:, k].sum() / table.sum()) for k, n in enumerate(("clear", "thick", "thin", "shadow"))},
             "bare_called_cloud_thick": share((5,), (1,)), "bare_called_cloud_any": share((5,), (1, 2)),
             "water_called_cloud_thick": share((6,), (1,)), "water_called_cloud_any": share((6,), (1, 2)),
             "veg_called_cloud_thick": share((4,), (1,)),
             "scl_high_cloud_caught_any": share((9,), (1, 2)), "scl_cloud_caught_any": share((8, 9), (1, 2)),
             "scl_cloud_caught_thick": share((8, 9), (1,)),
             "table_scl_by_ocm": table.tolist()}
        _panel(rgn, scl, ocm, panels / f"ocm_nigeria_{w['name']}.png")
        rows.append(r); shutil.rmtree(d)
        print(f"{w['name']}: {meta['scene_id']} bare->thick {r['bare_called_cloud_thick']} water->thick "
              f"{r['water_called_cloud_thick']} SCL9 caught {r['scl_high_cloud_caught_any']}", flush=True)
    ok = [r for r in rows if "failed" not in r]
    clear = [r for r in ok if r["kind"] == "clear"]; cloudy = [r for r in ok if r["kind"] == "cloudy"]
    pooled = lambda rs, s, o: (lambda t: float(t[s][:, o].sum() / t[s].sum()) if t[s].sum() else None)(
        sum(np.array(r["table_scl_by_ocm"]) for r in rs)) if rs else None
    summary = {"clear_bare_called_cloud_thick": pooled(clear, [5], [1]),
               "clear_water_called_cloud_thick": pooled(clear, [6], [1]),
               "cloudy_scl_high_cloud_caught_any": pooled(cloudy, [9], [1, 2]),
               "cloudy_scl_cloud_caught_any": pooled(cloudy, [8, 9], [1, 2])}
    marks = {"clear bare <= 1%": summary["clear_bare_called_cloud_thick"] is not None and summary["clear_bare_called_cloud_thick"] <= 0.01,
             "clear water <= 2%": summary["clear_water_called_cloud_thick"] is not None and summary["clear_water_called_cloud_thick"] <= 0.02,
             "cloudy SCL 9 caught >= 80%": summary["cloudy_scl_high_cloud_caught_any"] is not None and summary["cloudy_scl_high_cloud_caught_any"] >= 0.8}
    res = {"protocol": __doc__.split("Protocol, fixed before measuring (2026-11-14):")[1].split("Usage")[0].strip(),
           "summary": summary, "pass_marks": marks, "windows": rows}
    Path(out).write_text(json.dumps(res, indent=1))
    print(json.dumps({"summary": summary, "pass_marks": marks}, indent=1))


if __name__ == "__main__":
    {"run": run}[sys.argv[1]](*sys.argv[2:])
