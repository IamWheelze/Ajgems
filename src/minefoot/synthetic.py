"""Synthetic Sentinel-2-like scenes, used only to test the software.

Nothing here says anything about real mining, Nigeria or model performance.
"""
from __future__ import annotations
import hashlib, json
from pathlib import Path
import numpy as np
import rasterio
from rasterio.transform import from_origin
from .schema import BANDS

# Rough, invented spectra, ordered as BANDS.
SPECTRA = {
    "vegetation": [.03, .06, .04, .09, .24, .30, .34, .36, .19, .09],
    "soil": [.09, .14, .20, .23, .26, .28, .30, .32, .38, .34],
    "tailings": [.24, .30, .35, .37, .38, .39, .40, .41, .44, .42],
    "pit": [.13, .18, .25, .27, .29, .30, .31, .32, .36, .33],
    "water": [.06, .08, .06, .04, .03, .02, .02, .02, .01, .01],
    "farm": [.08, .13, .19, .21, .25, .27, .29, .30, .33, .28],
}


def make_scene(out_dir, baseline="05.10", offset=-1000, size=512, seed=7, cloud=True, scene_id=None):
    """Write raw_dn.tif, scene.json and truth.geojson. Returns the folder."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[:size, :size]
    s = size / 512
    cls = np.zeros((size, size), "uint8")
    wave = 40 * s * np.sin(yy / (60 * s))
    field = (np.abs(xx - 90 * s) < 60 * s) & (np.abs(yy - 410 * s) < 50 * s)
    river = np.abs(xx - (380 * s + wave)) < 6 * s
    pit = ((xx - 170 * s) ** 2 + (yy - 150 * s) ** 2) < (48 * s) ** 2
    tail = ((xx - 245 * s) ** 2 / 2 + (yy - 190 * s) ** 2) < (30 * s) ** 2
    names = list(SPECTRA)
    for mask, n in ((field, "farm"), (river, "water"), (tail, "tailings"), (pit, "pit")):
        cls[mask] = names.index(n)
    spectra = np.array([SPECTRA[n] for n in names])[cls]      # H, W, bands
    noise = rng.normal(0, .012, (size, size, len(BANDS)))
    refl = np.clip(spectra + noise, .001, 1)
    scl = np.select([cls == names.index("water"), cls == 0], [6, 4], 5).astype("uint8")
    # Tailings partly misclassified as snow, as bright surfaces can be.
    scl[tail & (xx > 245 * s)] = 11
    if cloud:
        c = ((xx - 430 * s) ** 2 + (yy - 90 * s) ** 2) < (75 * s) ** 2
        shadow = ((xx - 395 * s) ** 2 + (yy - 150 * s) ** 2) < (55 * s) ** 2
        scl[shadow & ~c] = 3
        scl[c] = 9
        refl[c] = np.minimum(refl[c] + .45, 1)
    nd = yy > size - 18 * s
    scl[nd] = 0
    dn = np.rint(refl * 10000 - offset).astype("uint16")
    dn[nd] = 0
    scene_id = scene_id or f"SYNTHETIC_baseline{baseline.replace('.', '')}_seed{seed}"
    out = Path(out_dir) / scene_id
    out.mkdir(parents=True, exist_ok=True)
    tf = from_origin(600000, 1100000, 10, 10)
    with rasterio.open(out / "raw_dn.tif", "w", driver="GTiff", height=size, width=size, count=len(BANDS) + 1,
                       dtype="uint16", crs="EPSG:32632", transform=tf, nodata=0, compress="deflate") as f:
        for i, b in enumerate(BANDS, 1):
            f.write(dn[:, :, i - 1], i); f.set_band_description(i, b)
        f.write(scl.astype("uint16"), len(BANDS) + 1); f.set_band_description(len(BANDS) + 1, "SCL")
    rad = {"offset_dn": offset, "quantification": 10000.0, "rule": "synthetic", "baseline": baseline}
    meta = {"scene_id": scene_id, "provider": "synthetic", "collection": "synthetic", "datetime": "2026-01-14T10:00:00Z",
            "sensor": "synthetic", "processing_level": "L2A", "processing_baseline": baseline, "cloud_pct_scene": None,
            "retrieved_at": None, "radiometry": {b: rad for b in BANDS},
            "licence_note": "Synthetic test data, not real imagery", "synthetic": True,
            "sha256_raw": hashlib.sha256((out / "raw_dn.tif").read_bytes()).hexdigest()}
    (out / "scene.json").write_text(json.dumps(meta, indent=2))
    from rasterio.features import shapes
    feats = [{"type": "Feature", "properties": {"label": "synthetic_disturbance"}, "geometry": g}
             for g, v in shapes((pit | tail).astype("uint8"), transform=tf) if v == 1]
    (out / "truth.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": feats,
        "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::32632"}}}))
    return out


def make_safe_zip(raw_dir, zip_path, baseline="05.11", offset=-1000):
    """A zipped L2A .SAFE built from a synthetic scene, for upload tests. Bands are GeoTIFF bytes under .jp2 names
    (GDAL recognises the content, not the extension); 20 m bands stay on the 10 m grid, which fetch_scene aligns anyway."""
    import io
    import zipfile
    from .upload import BAND_FILES, BAND_INDEX
    raw_dir, zip_path = Path(raw_dir), Path(zip_path)
    meta = json.loads((raw_dir / "scene.json").read_text())
    safe = f"S2B_MSIL2A_20260114T100000_N{baseline.replace('.', '')}_R000_T32PMR_20260114T120000.SAFE"
    granule = f"{safe}/GRANULE/L2A_T32PMR_A000000_20260114T100000/IMG_DATA"
    offs = "".join(f'<BOA_ADD_OFFSET band_id="{i}">{offset}</BOA_ADD_OFFSET>' for i in range(13))
    mtd = (f'<?xml version="1.0"?><n1:Level-2A_User_Product xmlns:n1="https://psd-14.sentinel2.eo.esa.int/PSD/User_Product_Level-2A.xsd">'
           f"<General_Info><Product_Info><PRODUCT_START_TIME>{meta['datetime']}</PRODUCT_START_TIME>"
           f"<PROCESSING_BASELINE>{baseline}</PROCESSING_BASELINE><Datatake><SPACECRAFT_NAME>Sentinel-2B</SPACECRAFT_NAME></Datatake>"
           f"</Product_Info><Product_Image_Characteristics><QUANTIFICATION_VALUES_LIST><BOA_QUANTIFICATION_VALUE>10000"
           f"</BOA_QUANTIFICATION_VALUE></QUANTIFICATION_VALUES_LIST><BOA_ADD_OFFSET_VALUES_LIST>{offs}</BOA_ADD_OFFSET_VALUES_LIST>"
           f"</Product_Image_Characteristics></General_Info><Quality_Indicators_Info><Cloud_Coverage_Assessment>12.5"
           f"</Cloud_Coverage_Assessment></Quality_Indicators_Info></n1:Level-2A_User_Product>")
    with rasterio.open(raw_dir / "raw_dn.tif") as src, zipfile.ZipFile(zip_path, "w", zipfile.ZIP_STORED) as z:
        names = list(src.descriptions)
        z.writestr(f"{safe}/MTD_MSIL2A.xml", mtd)
        for band, (res, suffix) in BAND_FILES.items():
            buf = io.BytesIO()
            p = {**src.profile, "count": 1, "driver": "GTiff", "tiled": False}
            p.pop("blockxsize", None); p.pop("blockysize", None)
            with rasterio.io.MemoryFile() as mf:
                with mf.open(**p) as d:
                    d.write(src.read(names.index(band) + 1), 1)
                buf.write(mf.read())
            z.writestr(f"{granule}/{res}/T32PMR_20260114T100000_{suffix}.jp2", buf.getvalue())
    return zip_path
