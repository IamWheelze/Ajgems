"""Digital numbers to surface reflectance (FR-032).

The offset depends on the product's processing baseline, never on its date:
reprocessed archive products can carry the offset for old acquisitions.
Catalogues also differ: some serve pixels that have already been shifted.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Mapping
import numpy as np

QUANTIFICATION = 10000.0
NODATA_DN = 0


class MissingRadiometry(ValueError):
    """Offset cannot be established safely, so the scene must not be used."""


@dataclass(frozen=True)
class Radiometry:
    offset_dn: int
    quantification: float
    rule: str
    baseline: str | None


def baseline_at_least(baseline: str, major: int, minor: int = 0) -> bool:
    a, b = str(baseline).strip().split(".")[:2]
    return (int(a), int(b)) >= (major, minor)


def resolve(properties: Mapping[str, Any], asset: Mapping[str, Any] | None = None) -> Radiometry:
    """Work out the offset from STAC metadata, most explicit evidence first."""
    baseline = properties.get("s2:processing_baseline")
    if properties.get("earthsearch:boa_offset_applied") is True:
        # Provider states the pixels were already shifted.
        return Radiometry(0, QUANTIFICATION, "provider_offset_already_applied", baseline)
    for band in (asset or {}).get("raster:bands", []) or []:
        if "offset" in band and "scale" in band and band["scale"]:
            return Radiometry(round(band["offset"] / band["scale"]), round(1 / band["scale"]),
                              "asset_raster_bands", baseline)
    if baseline is None:
        raise MissingRadiometry("no processing baseline or asset offset in metadata")
    offset = -1000 if baseline_at_least(baseline, 4, 0) else 0
    return Radiometry(offset, QUANTIFICATION, "processing_baseline", baseline)


def to_reflectance(dn: np.ndarray, r: Radiometry) -> np.ndarray:
    """Convert DN to float32 reflectance. DN 0 means no data and becomes NaN."""
    out = (dn.astype("float32") + r.offset_dn) / r.quantification
    out[dn == NODATA_DN] = np.nan
    return out
