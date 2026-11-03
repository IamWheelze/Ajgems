"""The one preprocessing path, shared by training and inference (FR-033)."""
from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.warp import reproject
from . import PREPROCESSING_VERSION
from .quality import coverage, usable_mask
from .reflectance import Radiometry, to_reflectance
from .schema import BANDS, SCHEMA_VERSION, validate_reflectance


def align(src_arr, src_transform, crs, dst_shape, dst_transform, nearest=False):
    """Resample one band onto the 10 m grid. Quality classes use nearest neighbour."""
    dst = np.zeros(dst_shape, dtype=src_arr.dtype)
    reproject(src_arr, dst, src_transform=src_transform, src_crs=crs, dst_transform=dst_transform,
              dst_crs=crs, src_nodata=0, dst_nodata=0,
              resampling=Resampling.nearest if nearest else Resampling.bilinear)
    return dst


def to_model_array(raw_dn: np.ndarray, scl: np.ndarray, radiometry: dict[str, Radiometry]) -> np.ndarray:
    """(bands, H, W) aligned DN and SCL to float32 reflectance. Unusable pixels become NaN."""
    out = np.stack([to_reflectance(raw_dn[i], radiometry[b]) for i, b in enumerate(BANDS)])
    out[:, ~usable_mask(scl)] = np.nan
    validate_reflectance(out)
    return out


def prepare_scene(raw_dir: Path, out_dir: Path) -> dict:
    """Read a fetched scene folder and write reflectance.tif, scl.tif and prepared.json."""
    raw_dir, out_dir = Path(raw_dir), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    meta = json.loads((raw_dir / "scene.json").read_text())
    radiometry = {b: Radiometry(**meta["radiometry"][b]) for b in BANDS}
    with rasterio.open(raw_dir / "raw_dn.tif") as src:
        names = list(src.descriptions)
        order = [names.index(b) + 1 for b in BANDS]
        raw = src.read(order)
        scl = src.read(names.index("SCL") + 1)
        profile = src.profile
    arr = to_model_array(raw, scl, radiometry)
    profile.update(count=len(BANDS), dtype="float32", nodata=float("nan"), compress="deflate",
                   tiled=True, blockxsize=256, blockysize=256)
    with rasterio.open(out_dir / "reflectance.tif", "w", **profile) as dst:
        dst.write(arr)
        for i, b in enumerate(BANDS, 1):
            dst.set_band_description(i, b)
    p2 = {**profile, "count": 1, "dtype": "uint8", "nodata": None}
    with rasterio.open(out_dir / "scl.tif", "w", **p2) as dst:
        dst.write(scl.astype("uint8"), 1)
    info = {**meta, "input_schema": SCHEMA_VERSION, "preprocessing_version": PREPROCESSING_VERSION,
            "coverage": coverage(scl).as_dict()}
    (out_dir / "prepared.json").write_text(json.dumps(info, indent=2))
    return info


def iter_chips(height: int, width: int, size: int = 512, overlap: int = 64):
    """Yield (row, col) so chips cover everything, including the last row and column."""
    if overlap >= size:
        raise ValueError("overlap must be smaller than chip size")
    def starts(n):
        if n <= size:
            return [0]
        s = list(range(0, n - size, size - overlap))
        return s + [n - size]
    for r in starts(height):
        for c in starts(width):
            yield r, c
