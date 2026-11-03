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
