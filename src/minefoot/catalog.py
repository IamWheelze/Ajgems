"""Scene search and windowed retrieval through STAC (FR-020, FR-021, FR-023, FR-024)."""
from __future__ import annotations
import hashlib, json, time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
import numpy as np

from .reflectance import resolve
from .schema import BANDS, BANDS_10M, PIXEL_SIZE_M

# Each catalogue names its assets differently. Names must be confirmed by `minefoot doctor`.
PROVIDERS = {
    "planetary": {"url": "https://planetarycomputer.microsoft.com/api/stac/v1", "collection": "sentinel-2-l2a",
                  "assets": {b: b for b in (*BANDS, "SCL")}, "sign": True},
    "earth-search": {"url": "https://earth-search.aws.element84.com/v1", "collection": "sentinel-2-c1-l2a",
                     "assets": {"B02": "blue", "B03": "green", "B04": "red", "B05": "rededge1", "B06": "rededge2",
                                "B07": "rededge3", "B08": "nir", "B8A": "nir08", "B11": "swir16", "B12": "swir22",
                                "SCL": "scl"}, "sign": False},
}


def _client(provider: str):
    from pystac_client import Client
    cfg = PROVIDERS[provider]
    modifier = None
    if cfg["sign"]:
        import planetary_computer
        modifier = planetary_computer.sign_inplace
    return Client.open(cfg["url"], modifier=modifier)


def search(bbox, start: str, end: str, max_cloud: float = 40.0, providers=("planetary", "earth-search"),
           limit: int = 200, attempts: int = 3):
    """Search in provider order. Falls back if one fails and records which one answered."""
    errors = {}
    for p in providers:
        for attempt in range(attempts):
            try:
                result = _client(p).search(collections=[PROVIDERS[p]["collection"]], bbox=list(bbox),
                                           datetime=f"{start}/{end}", max_items=limit,
                                           query={"eo:cloud_cover": {"lt": max_cloud}})
                items = sorted(result.items(), key=lambda i: (i.properties.get("eo:cloud_cover", 100), i.id))
                return p, items
            except Exception as exc:  # network and catalogue failures vary by library
                errors[p] = f"{type(exc).__name__}: {exc}"
                time.sleep(2 ** attempt)
    raise RuntimeError(f"all catalogues failed: {errors}")


def summarise(item) -> dict:
    x = item.properties
    return {"id": item.id, "datetime": x.get("datetime"), "cloud_pct": x.get("eo:cloud_cover"),
            "baseline": x.get("s2:processing_baseline"), "mgrs": x.get("s2:mgrs_tile") or x.get("grid:code"),
            "platform": x.get("platform")}


def fetch_scene(item, provider: str, bbox_wgs84, out_dir) -> Path:
    """Read only the requested window, align to 10 m, and store raw DN with full provenance."""
    import rasterio
    from rasterio.transform import from_origin
    from rasterio.warp import transform_bounds
    from rasterio.windows import from_bounds
    from .preprocess import align

    cfg = PROVIDERS[provider]
    out = Path(out_dir) / item.id
    out.mkdir(parents=True, exist_ok=True)
    arrays, radiometry, dst = {}, {}, None
    for band in BANDS_10M + tuple(b for b in (*BANDS, "SCL") if b not in BANDS_10M):
        asset = item.assets[cfg["assets"][band]]
        with rasterio.open(asset.href) as src:
            b = transform_bounds("EPSG:4326", src.crs, *bbox_wgs84, densify_pts=21)
            if dst is None:
                # 10 m grid snapped to the source pixels, so no shift is introduced.
                t = src.transform
                left = t.c + np.floor((b[0] - t.c) / t.a) * t.a
                top = t.f - np.floor((t.f - b[3]) / -t.e) * -t.e
                w = int(np.ceil((b[2] - left) / PIXEL_SIZE_M))
                h = int(np.ceil((top - b[1]) / PIXEL_SIZE_M))
                dst = dict(crs=src.crs, transform=from_origin(left, top, PIXEL_SIZE_M, PIXEL_SIZE_M), h=h, w=w)
            win = from_bounds(*[*_win_bounds(dst)], transform=src.transform).round_offsets().round_lengths()
            data = src.read(1, window=win, boundless=True, fill_value=0)
            arrays[band] = align(data, src.window_transform(win), src.crs, (dst["h"], dst["w"]), dst["transform"],
                                 nearest=band == "SCL")
        if band != "SCL":
            radiometry[band] = asdict(resolve(item.properties, asset.to_dict()))
    names = (*BANDS, "SCL")
    with rasterio.open(out / "raw_dn.tif", "w", driver="GTiff", height=dst["h"], width=dst["w"], count=len(names),
                       dtype="uint16", crs=dst["crs"], transform=dst["transform"], nodata=0, compress="deflate") as f:
        for i, n in enumerate(names, 1):
            f.write(arrays[n].astype("uint16"), i)
            f.set_band_description(i, n)
    meta = {"scene_id": item.id, "provider": provider, "collection": cfg["collection"],
            "datetime": item.properties.get("datetime"), "sensor": item.properties.get("platform"),
            "processing_level": "L2A", "processing_baseline": item.properties.get("s2:processing_baseline"),
            "cloud_pct_scene": item.properties.get("eo:cloud_cover"), "bbox_wgs84": list(bbox_wgs84),
            "retrieved_at": datetime.now(timezone.utc).isoformat(), "radiometry": radiometry,
            "licence_note": copernicus_notice(item.properties.get("datetime")), "synthetic": False,
            "sha256_raw": hashlib.sha256((out / "raw_dn.tif").read_bytes()).hexdigest()}
    (out / "scene.json").write_text(json.dumps(meta, indent=2))
    return out


def copernicus_notice(acquired: str | None) -> str:
    """Credit required by the Sentinel Data Legal Notice for modified data, with the acquisition year."""
    year = (acquired or "")[:4]
    return f"Contains modified Copernicus Sentinel data {year}" if year.isdigit() else "Contains modified Copernicus Sentinel data"


def _win_bounds(d):
    t = d["transform"]
    return t.c, t.f - d["h"] * PIXEL_SIZE_M, t.c + d["w"] * PIXEL_SIZE_M, t.f
