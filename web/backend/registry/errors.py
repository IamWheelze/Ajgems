"""Evaluation error maps in the app (FR-129): per-site true positive, false positive and missed pixels
from a registered run's stored error maps, ranked by worst pixel F1."""
import json
from pathlib import Path

import numpy as np
from django.conf import settings

# Okabe-Ito, checked for colour-vision-deficiency separation; each class also has its own toggle and label.
ERROR_CLASSES = {1: ("hit", (0, 114, 178)), 2: ("false_alarm", (230, 159, 0)), 3: ("missed", (204, 121, 167))}


def _f1(r):
    d = 2 * r["tp"] + r["fp"] + r["fn"]
    return 2 * r["tp"] / d if d else None


def site_rows(mv, split="test"):
    run = Path(mv.run_path)
    m = json.loads((run / "metrics.json").read_text())["models"]["unet"][split]["per_site"]
    rows = []
    for r in m:
        emap = run / "error_maps" / f"{r['site_id']}__unet.tif"
        if not emap.exists():
            continue
        p = r["tp"] / (r["tp"] + r["fp"]) if r["tp"] + r["fp"] else None
        rc = r["tp"] / (r["tp"] + r["fn"]) if r["tp"] + r["fn"] else None
        rows.append({"site_id": r["site_id"], "f1": _f1(r), "precision": p, "recall": rc,
                     "mine_scale": r.get("mine_scale"), "mine_type": r.get("mine_type"), "region": r.get("region"),
                     "false_candidates": r.get("false_candidates"), "label_footprints": r.get("label_footprints"),
                     "tp": r["tp"], "fp": r["fp"], "fn": r["fn"]})
    # Worst first; a site with no mining and no prediction (F1 undefined) goes last.
    rows.sort(key=lambda x: (x["f1"] is None, x["f1"] if x["f1"] is not None else 0, -(x["fp"] + x["fn"])))
    return rows


def render(mv, site_id, layer, classes=("hit", "false_alarm", "missed", "not_assessed")):
    """'errors' (RGBA classes, background transparent, not-assessed hatched) or 'true_colour', warped to WGS 84."""
    import rasterio
    from PIL import Image
    from rasterio.warp import Resampling, calculate_default_transform, reproject
    from minefoot.viewer import STRETCH, rgb
    from minefoot.schema import BANDS
    if layer not in ("errors", "true_colour"):
        raise ValueError("layer must be errors or true_colour")
    run = Path(mv.run_path)
    if not any(r["site_id"] == site_id for r in site_rows(mv)):
        raise ValueError("no error map for this site")
    classes = sorted(set(classes) & {"hit", "false_alarm", "missed", "not_assessed"})
    tag = layer if layer == "true_colour" else f"{layer}_{'-'.join(classes) or 'none'}"
    cache = settings.MINEFOOT["IMAGE_CACHE"] / f"err_{mv.pk}_{site_id}_{tag}.png"
    meta = cache.with_suffix(".bounds")
    if cache.exists() and meta.exists():
        return cache, [float(v) for v in meta.read_text().split(",")]
    cfg = json.loads((run / "config.json").read_text())
    if layer == "errors":
        with rasterio.open(run / "error_maps" / f"{site_id}__unet.tif") as s:
            e, t, crs = s.read(1), s.transform, s.crs
        h, w = e.shape
        img = np.zeros((h, w, 4), "uint8")
        for k, (name, c) in ERROR_CLASSES.items():
            if name in classes:
                img[e == k] = (*c, 230)
        if "not_assessed" in classes:
            yy, xx = np.mgrid[:h, :w]
            na = e == 4
            img[na & (((xx + yy) % 6) < 2)] = (235, 235, 235, 220)
            img[na & (((xx + yy) % 6) >= 2)] = (60, 60, 60, 90)
    else:
        prep = settings.MINEFOOT["DATA_ROOT"] / "prepared" / cfg["dataset"]["name"] / site_id
        with rasterio.open(prep / "reflectance.tif") as s:
            arr, t, crs = s.read(), s.transform, s.crs
        img = np.dstack([rgb(arr, list(BANDS), *STRETCH["true_colour"]), np.full(arr.shape[1:], 255, "uint8")])
    h, w = img.shape[:2]
    dst_t, dw, dh = calculate_default_transform(crs, "EPSG:4326", w, h, *rasterio.transform.array_bounds(h, w, t))
    out = np.zeros((4, dh, dw), "uint8")
    for b in range(4):
        reproject(img[..., b], out[b], src_transform=t, src_crs=crs, dst_transform=dst_t, dst_crs="EPSG:4326",
                  resampling=Resampling.nearest)
    cache.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(out.transpose(1, 2, 0), "RGBA").save(cache, optimize=True)
    west, north = dst_t * (0, 0); east, south = dst_t * (dw, dh)
    bounds = [west, south, east, north]
    meta.write_text(",".join(map(str, bounds)))
    return cache, bounds
