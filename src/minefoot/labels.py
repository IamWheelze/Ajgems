"""Label loading and rasterising, plus a reader for the Global ML-ready file."""
from __future__ import annotations
from pathlib import Path
import numpy as np

# Global ML-ready v1.0.0, checked against the released file on 2026-11-03: the `tiles`
# layer has these columns; label layers have only tile_id and geometry.
EXPECTED_COLUMNS = {"tiles": {"tile_id", "s2_tile_id", "split"}}
EXPECTED_LABEL_COLUMNS = {"tile_id"}


def read_vectors(path, layer=None):
    import geopandas as gpd
    return gpd.read_file(path, layer=layer) if layer else gpd.read_file(path)


def rasterize_to(raster_path, gdf) -> np.ndarray:
    """Burn polygons onto a raster's grid. Reprojects labels to the raster's CRS."""
    import rasterio
    from rasterio.features import rasterize
    with rasterio.open(raster_path) as src:
        shapes = [(g, 1) for g in gdf.to_crs(src.crs).geometry if g is not None and not g.is_empty]
        if not shapes:
            return np.zeros((src.height, src.width), "uint8")
        return rasterize(shapes, out_shape=(src.height, src.width), transform=src.transform, dtype="uint8")


def inspect_dataset(path) -> dict:
    """Report what a dataset file actually contains, before any code relies on it."""
    import pyogrio
    report = {"path": str(path), "layers": {}}
    for name, _ in pyogrio.list_layers(path):
        info = pyogrio.read_info(path, layer=name)
        cols = set(info["fields"].tolist())
        report["layers"][name] = {"features": int(info["features"]), "crs": info["crs"],
                                  "columns": sorted(cols),
                                  "missing_expected": sorted(EXPECTED_COLUMNS.get(name, EXPECTED_LABEL_COLUMNS) - cols)}
    return report


def audit_labels(prepared_dir, labels_path) -> dict:
    """Mask rates inside versus outside the labels for one prepared scene."""
    import rasterio
    from .quality import mask_rates
    prepared_dir = Path(prepared_dir)
    inside = rasterize_to(prepared_dir / "reflectance.tif", read_vectors(labels_path))
    with rasterio.open(prepared_dir / "scl.tif") as src:
        scl = src.read(1)
    return {"scene": prepared_dir.name, **mask_rates(scl, inside)}
