"""Masks, usable coverage and tile outcomes (FR-050 to FR-053)."""
from __future__ import annotations
from dataclasses import dataclass, asdict
from enum import Enum
import numpy as np

SCL = {"no_data": 0, "defective": 1, "dark": 2, "cloud_shadow": 3, "vegetation": 4, "bare": 5,
       "water": 6, "unclassified": 7, "cloud_medium": 8, "cloud_high": 9, "cirrus": 10, "snow": 11}
NOT_USABLE = (0, 1, 3, 8, 9, 10)
# Snow does not occur at Nigerian elevations, so this class signals a bright surface
# misclassified by the scene classifier. Tailings are at risk of this. Keep it, and flag it.
SUSPECT_BRIGHT = (11,)

WARN_BELOW = 0.80   # provisional, see FR-053
BLOCK_BELOW = 0.40  # provisional, see FR-053


class Outcome(str, Enum):
    CANDIDATE_FOUND = "candidate_found"
    NO_CANDIDATE = "no_candidate_found_in_usable_imagery"
    INSUFFICIENT = "insufficient_usable_imagery"
    NOT_PROCESSED = "not_processed"


def usable_mask(scl: np.ndarray) -> np.ndarray:
    return ~np.isin(scl, NOT_USABLE)


@dataclass(frozen=True)
class Coverage:
    total_pixels: int
    usable_fraction: float
    cloud_fraction: float
    shadow_fraction: float
    nodata_fraction: float
    suspect_bright_fraction: float
    decision: str  # pass | warn | block

    def as_dict(self) -> dict:
        return asdict(self)


def coverage(scl: np.ndarray, warn_below=WARN_BELOW, block_below=BLOCK_BELOW) -> Coverage:
    n = int(scl.size)
    if n == 0:
        return Coverage(0, 0.0, 0.0, 0.0, 1.0, 0.0, "block")
    frac = lambda *c: float(np.isin(scl, c).sum()) / n
    usable = float(usable_mask(scl).sum()) / n
    decision = "block" if usable < block_below else "warn" if usable < warn_below else "pass"
    return Coverage(n, usable, frac(8, 9, 10), frac(3), frac(0, 1), frac(*SUSPECT_BRIGHT), decision)


def tile_outcome(scl: np.ndarray | None, candidate_pixels: int | None,
                 block_below: float = BLOCK_BELOW) -> Outcome:
    """Never report 'no candidate' for imagery the model could not see."""
    if scl is None or candidate_pixels is None:
        return Outcome.NOT_PROCESSED
    if coverage(scl, block_below=block_below).decision == "block":
        return Outcome.INSUFFICIENT
    return Outcome.CANDIDATE_FOUND if candidate_pixels > 0 else Outcome.NO_CANDIDATE


def mask_rates(scl: np.ndarray, inside: np.ndarray) -> dict:
    """Share of pixels in each mask group, inside and outside a label mask.

    Used to audit whether the scene classifier removes labelled ground more often
    than its surroundings, e.g. bright tailings classed as cloud or snow.
    """
    groups = {"usable": None, "cloud": (8, 9, 10), "cloud_medium": (8,), "shadow": (3,),
              "no_data": (0, 1), "snow": SUSPECT_BRIGHT}
    inside = inside.astype(bool)
    out = {"inside_pixels": int(inside.sum()), "outside_pixels": int((~inside).sum())}
    for where, sel in (("inside", inside), ("outside", ~inside)):
        v = scl[sel]
        for g, codes in groups.items():
            hit = usable_mask(v) if codes is None else np.isin(v, codes)
            out[f"{where}_{g}"] = float(hit.mean()) if v.size else float("nan")
    return out


# Per-polygon masking (candidates, labels or areas of interest). Provisional, like FR-053.
# Bright mine ground is often classed as cloud (docs/m1_global_ml_ready_check.md), so a
# scene can pass overall while the pixels inside a polygon were not seen.
POLYGON_PARTLY_MASKED = 0.05   # masked share at or above this: "partly_masked"
POLYGON_MOSTLY_MASKED = 0.50   # masked share at or above this: "mostly_masked"


class PolygonStatus(str, Enum):
    ASSESSED = "assessed"
    PARTLY_MASKED = "partly_masked"
    MOSTLY_MASKED = "mostly_masked"
    OUTSIDE_IMAGE = "outside_image"


def polygon_coverage(scl_values: np.ndarray, partly=POLYGON_PARTLY_MASKED, mostly=POLYGON_MOSTLY_MASKED) -> dict:
    """Masking summary for the SCL values of the pixels inside one polygon."""
    v = np.asarray(scl_values).ravel()
    n = int(v.size)
    if n == 0:
        return {"pixels": 0, "usable_fraction": None, "masked_fraction": None, "cloud_fraction": None,
                "cloud_medium_fraction": None, "shadow_fraction": None, "nodata_fraction": None,
                "snow_fraction": None, "mask_status": PolygonStatus.OUTSIDE_IMAGE.value}
    frac = lambda *c: float(np.isin(v, c).mean())
    usable = float(usable_mask(v).mean())
    masked = 1.0 - usable
    status = (PolygonStatus.MOSTLY_MASKED if masked >= mostly else
              PolygonStatus.PARTLY_MASKED if masked >= partly else PolygonStatus.ASSESSED)
    return {"pixels": n, "usable_fraction": usable, "masked_fraction": masked, "cloud_fraction": frac(8, 9, 10),
            "cloud_medium_fraction": frac(8), "shadow_fraction": frac(3), "nodata_fraction": frac(0, 1),
            "snow_fraction": frac(*SUSPECT_BRIGHT), "mask_status": status.value}
