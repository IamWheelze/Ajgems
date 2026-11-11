"""Drift measures (FR-105): how far a job's inputs and outputs are from what the model was trained and checked on.

- Input: per-band mean reflectance of a tile's usable pixels, as a shift in training standard deviations from the
  training mean (normalisation.json, fitted on training pixels only).
- Output: candidate area per 1,000 km2 of usable imagery, against the model's own rate on its validation sites.
These are monitoring signals with provisional thresholds, not accuracy measures.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from .quality import usable_mask
from .schema import BANDS

PIXEL_KM2 = 1e-4   # one 10 m pixel


def input_stats(prepared_dir) -> dict:
    import rasterio
    with rasterio.open(Path(prepared_dir) / "reflectance.tif") as s, rasterio.open(Path(prepared_dir) / "scl.tif") as q:
        arr, scl = s.read(), q.read(1)
    ok = usable_mask(scl) & np.isfinite(arr).all(axis=0)
    n = int(ok.sum())
    return {"usable_pixels": n, "means": {b: (float(arr[i][ok].mean()) if n else None) for i, b in enumerate(BANDS)}}


def shift(means: dict, run_dir) -> dict:
    """Per band: (tile mean - training mean) / training standard deviation."""
    norm = json.loads((Path(run_dir) / "normalisation.json").read_text())
    out = {}
    for b, mu, sd in zip(norm["bands"], norm["mean"], norm["std"]):
        v = means.get(b)
        out[b] = None if v is None or not sd else (v - mu) / sd
    return out


def reference_rate(run_dir) -> float | None:
    """Predicted km2 per 1,000 km2 of usable imagery on the run's validation sites."""
    rows = json.loads((Path(run_dir) / "metrics.json").read_text())["models"]["unet"]["val"]["per_site"]
    valid = sum(r.get("valid_km2") or 0 for r in rows)
    return 1000 * sum(r["tp"] + r["fp"] for r in rows) * PIXEL_KM2 / valid if valid else None
