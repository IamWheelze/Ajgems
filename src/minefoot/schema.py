"""Versioned model input schema (FR-030)."""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np

SCHEMA_VERSION = "s2-l2a-10band-v1"
BANDS_10M = ("B02", "B03", "B04", "B08")
BANDS_20M = ("B05", "B06", "B07", "B8A", "B11", "B12")
# Fixed order: every training and inference array follows this order.
BANDS = ("B02", "B03", "B04", "B05", "B06", "B07", "B08", "B8A", "B11", "B12")
QUALITY_BAND = "SCL"
PIXEL_SIZE_M = 10.0


class SchemaError(ValueError):
    """Input does not satisfy the model input schema."""


@dataclass(frozen=True)
class RasterFacts:
    band_names: tuple[str, ...]
    dtype: str
    pixel_size: float
    crs_is_projected: bool
    has_crs: bool
    processing_level: str


def validate_raw(f: RasterFacts) -> None:
    """Raise SchemaError naming the first reason this raster is unusable."""
    missing = [b for b in (*BANDS, QUALITY_BAND) if b not in f.band_names]
    if missing:
        raise SchemaError(f"missing bands: {missing}")
    if f.processing_level != "L2A":
        raise SchemaError(f"processing level must be L2A, got {f.processing_level!r}")
    if not f.has_crs:
        raise SchemaError("raster has no CRS")
    if not f.crs_is_projected:
        raise SchemaError("raster CRS must be projected (UTM expected)")
    if abs(f.pixel_size - PIXEL_SIZE_M) > 1e-6:
        raise SchemaError(f"pixel size must be {PIXEL_SIZE_M} m, got {f.pixel_size}")
    if f.dtype not in ("uint16", "int32"):
        raise SchemaError(f"raw digital numbers must be integer, got {f.dtype}")


def validate_reflectance(arr: np.ndarray) -> None:
    """Sanity-check reflectance. Catches an unapplied or doubly applied offset."""
    if arr.ndim != 3 or arr.shape[0] != len(BANDS):
        raise SchemaError(f"expected ({len(BANDS)}, H, W), got {arr.shape}")
    valid = arr[np.isfinite(arr)]
    if valid.size == 0:
        return
    if np.median(valid) > 1.0 or np.percentile(valid, 99) > 2.0:
        raise SchemaError("values look like digital numbers, not reflectance")
