"""Uploaded Sentinel-2 L2A products (FR-022): a zipped .SAFE folder as downloaded from Copernicus.

The zip is read in place through GDAL's /vsizip, so a job reads only the window it needs, and the scene
then goes through the same fetch_scene -> prepare_scene path as catalogue imagery (one preprocessing path).
Radiometry comes from the product's own metadata (BOA_ADD_OFFSET and BOA_QUANTIFICATION_VALUE), never from
the acquisition date.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from .schema import BANDS

# Band -> resolution folder and file suffix inside GRANULE/*/IMG_DATA.
BAND_FILES = {"B02": ("R10m", "B02_10m"), "B03": ("R10m", "B03_10m"), "B04": ("R10m", "B04_10m"), "B08": ("R10m", "B08_10m"),
              "B05": ("R20m", "B05_20m"), "B06": ("R20m", "B06_20m"), "B07": ("R20m", "B07_20m"), "B8A": ("R20m", "B8A_20m"),
              "B11": ("R20m", "B11_20m"), "B12": ("R20m", "B12_20m"), "SCL": ("R20m", "SCL_20m")}
BAND_INDEX = {"B01": 0, "B02": 1, "B03": 2, "B04": 3, "B05": 4, "B06": 5, "B07": 6, "B08": 7, "B8A": 8, "B09": 9,
              "B10": 10, "B11": 11, "B12": 12}   # band_id order in MTD_MSIL2A.xml


class NotCompatible(ValueError):
    """The file is not a Sentinel-2 L2A product this pipeline can use; the message says why."""


def _text(root, tag):
    el = next((e for e in root.iter() if e.tag.split("}")[-1] == tag), None)
    return el.text.strip() if el is not None and el.text else None


def read_safe(zip_path) -> dict:
    """Validate a zipped L2A .SAFE and return its metadata, band paths and footprint."""
    import rasterio
    from rasterio.warp import transform_bounds
    zip_path = Path(zip_path)
    try:
        z = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile:
        raise NotCompatible("not a zip file; upload the zipped .SAFE folder")
    names = z.namelist()
    mtd = next((n for n in names if re.search(r"\.SAFE/MTD_MSIL2A\.xml$", n)), None)
    if mtd is None:
        if any(n.endswith("MTD_MSIL1C.xml") for n in names):
            raise NotCompatible("this is a Level-1C product; the model needs Level-2A surface reflectance")
        raise NotCompatible("no MTD_MSIL2A.xml found; upload a zipped Sentinel-2 L2A .SAFE folder")
    root = ET.fromstring(z.read(mtd))
    baseline = _text(root, "PROCESSING_BASELINE")
    quant = float(_text(root, "BOA_QUANTIFICATION_VALUE") or 10000)
    offsets = {}
    for el in root.iter():
        if el.tag.split("}")[-1] == "BOA_ADD_OFFSET":
            offsets[int(el.attrib["band_id"])] = float(el.text)
    paths = {}
    for band, (res, suffix) in BAND_FILES.items():
        hit = [n for n in names if f"/IMG_DATA/{res}/" in n and n.endswith(f"_{suffix}.jp2")]
        if not hit:
            raise NotCompatible(f"band file {suffix} is missing")
        paths[band] = hit[0]
    scene_id = Path(mtd).parent.name.removesuffix(".SAFE")
    with rasterio.open(f"/vsizip/{zip_path}/{paths['B04']}") as s:
        w, so, e, n = transform_bounds(s.crs, "EPSG:4326", *s.bounds, densify_pts=21)
        crs = str(s.crs)
    cloud = _text(root, "Cloud_Coverage_Assessment")
    return {"scene_id": scene_id, "datetime": _text(root, "PRODUCT_START_TIME"), "processing_baseline": baseline,
            "platform": (_text(root, "SPACECRAFT_NAME") or "").lower(), "cloud_pct": float(cloud) if cloud else None,
            "quantification": quant, "offsets": {b: offsets.get(BAND_INDEX[b], 0.0) for b in BANDS},
            "paths": paths, "bbox_wgs84": [w, so, e, n], "crs": crs}


def stac_item(zip_path, meta) -> dict:
    """A STAC item for fetch_scene: assets point into the zip; radiometry from the product's own offsets."""
    w, s, e, n = meta["bbox_wgs84"]
    assets = {}
    for band, p in meta["paths"].items():
        a = {"href": f"/vsizip/{zip_path}/{p}", "roles": ["data"]}
        if band != "SCL":
            a.update({"raster:offset": meta["offsets"][band] / meta["quantification"], "raster:scale": 1 / meta["quantification"]})
        assets[band] = a
    return {"type": "Feature", "stac_version": "1.0.0", "id": meta["scene_id"], "links": [], "bbox": [w, s, e, n],
            "geometry": {"type": "Polygon", "coordinates": [[[w, s], [e, s], [e, n], [w, n], [w, s]]]},
            "properties": {"datetime": meta["datetime"], "platform": meta["platform"], "eo:cloud_cover": meta["cloud_pct"],
                           "processing:version": meta["processing_baseline"], "minefoot:provider": "upload"},
            "assets": assets}
