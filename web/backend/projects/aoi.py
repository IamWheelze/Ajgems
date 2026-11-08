"""Area-of-interest parsing and validation (FR-011 to FR-013).

Accepts drawn GeoJSON or an uploaded GeoJSON, KML, zipped Shapefile or GeoPackage.
Every refusal names its reason, as FR-012 requires.
"""
import json
import tempfile
import zipfile
from pathlib import Path

from django.contrib.gis.geos import GEOSGeometry, MultiPolygon


class AoiError(ValueError):
    pass


def geodesic_km2(shapely_geom) -> float:
    """Area on the WGS 84 ellipsoid, accurate everywhere (FR-011's 0.1% test)."""
    from pyproj import Geod
    area, _ = Geod(ellps="WGS84").geometry_area_perimeter(shapely_geom)
    return abs(area) / 1e6


def _validate(shapes, max_km2):
    from shapely.geometry import MultiPolygon as SMP, Polygon as SP
    from shapely.ops import unary_union
    from shapely.validation import explain_validity
    polys = []
    for g in shapes:
        if g is None or g.is_empty:
            continue
        if g.geom_type not in ("Polygon", "MultiPolygon"):
            raise AoiError(f"areas must be polygons, got {g.geom_type}")
        if not g.is_valid:
            raise AoiError(f"invalid geometry: {explain_validity(g)} (for example a self-intersection)")
        polys.extend(g.geoms if g.geom_type == "MultiPolygon" else [g])
    if not polys:
        raise AoiError("no polygon found")
    merged = unary_union(polys)
    merged = merged if merged.geom_type == "MultiPolygon" else SMP([merged])
    km2 = geodesic_km2(merged)
    if km2 > max_km2:
        raise AoiError(f"area is {km2:,.0f} km2, above the {max_km2:,.0f} km2 limit per job; split it into smaller areas")
    return GEOSGeometry(merged.wkt, srid=4326), km2


def from_geojson(obj, max_km2):
    """Drawn or pasted GeoJSON (geometry, Feature or FeatureCollection). GeoJSON is WGS 84 by definition."""
    from shapely.geometry import shape
    if isinstance(obj, str):
        obj = json.loads(obj)
    t = obj.get("type")
    if t == "FeatureCollection":
        geoms = [f.get("geometry") for f in obj.get("features", [])]
    elif t == "Feature":
        geoms = [obj.get("geometry")]
    else:
        geoms = [obj]
    try:
        shapes = [shape(g) for g in geoms if g]
    except Exception as exc:
        raise AoiError(f"not valid GeoJSON geometry: {exc}")
    geom, km2 = _validate(shapes, max_km2)
    return geom, km2, "EPSG:4326"


def from_upload(uploaded_file, max_km2):
    """GeoJSON, KML, zipped Shapefile or GeoPackage. The file must state its CRS (except GeoJSON and KML)."""
    import geopandas as gpd
    name = uploaded_file.name.lower()
    suffix = Path(name).suffix
    if suffix not in (".geojson", ".json", ".kml", ".zip", ".gpkg"):
        raise AoiError("upload GeoJSON, KML, a zipped Shapefile or a GeoPackage")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / Path(name).name
        with open(path, "wb") as f:
            for chunk in uploaded_file.chunks():
                f.write(chunk)
        if suffix == ".zip":
            with zipfile.ZipFile(path) as z:
                bad = [n for n in z.namelist() if n.startswith("/") or ".." in n]
                if bad:
                    raise AoiError("zip contains unsafe paths")
                z.extractall(tmp)
            shp = list(Path(tmp).rglob("*.shp"))
            if not shp:
                raise AoiError("zip contains no .shp file")
            path = shp[0]
            if not path.with_suffix(".prj").exists():
                raise AoiError("shapefile has no .prj, so its coordinate system is unknown")
        try:
            g = gpd.read_file(path)
        except Exception as exc:
            raise AoiError(f"could not read the file: {exc}")
    if g.crs is None:
        raise AoiError("file has no coordinate system (CRS)")
    crs = g.crs.to_string()
    geom, km2 = _validate(list(g.to_crs(4326).geometry), max_km2)
    return geom, km2, crs


def as_multipolygon(geom):
    return geom if isinstance(geom, MultiPolygon) else MultiPolygon(geom)
