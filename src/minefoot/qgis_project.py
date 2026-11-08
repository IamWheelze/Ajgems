"""Saved QGIS project for an inspection package (FR-133, FR-120 to FR-123).

Writes QGIS project XML directly, so no QGIS Python bindings are needed. Paths
are relative, so the package folder can be zipped and opened elsewhere.
Display layers are already rendered to 8-bit RGB with the gallery's fixed
stretches, so QGIS shows them with no further stretch (FR-122). Styles are
also written as .qml files (FRD section 10: ship the styles as files).
"""
from __future__ import annotations
import html
import uuid
from pathlib import Path

# Shared with viewer.SCL_COLOURS; names follow the Sentinel-2 L2A scene classification.
SCL_CLASSES = [(0, "No data", (0, 0, 0)), (1, "Saturated or defective", (120, 0, 120)), (2, "Dark area", (60, 60, 60)),
               (3, "Cloud shadow", (70, 70, 110)), (4, "Vegetation", (60, 140, 60)), (5, "Bare soil", (190, 150, 100)),
               (6, "Water", (40, 90, 200)), (7, "Unclassified", (150, 150, 150)),
               (8, "Cloud, medium probability", (200, 200, 200)), (9, "Cloud, high probability", (255, 255, 255)),
               (10, "Thin cirrus", (150, 210, 240)), (11, "Snow (suspect bright ground)", (255, 150, 0))]
MASK_STATUS = [("assessed", "Assessed", (0, 200, 230)), ("partly_masked", "Partly masked", (255, 170, 0)),
               ("mostly_masked", "Mostly masked", (230, 0, 200)), ("outside_image", "Outside image", (120, 120, 120))]
DISPLAY_LAYERS = [("true_colour", "True colour (B4 B3 B2, 0 to 0.35)", True),
                  ("swir_false_colour", "SWIR false colour (B12 B8A B4, 0 to 0.5)", False),
                  ("nir_false_colour", "NIR false colour (B8 B4 B3, 0 to 0.5)", False),
                  ("ndvi", "NDVI (-0.2 to 0.8)", False), ("ndwi", "NDWI (-0.6 to 0.6)", False),
                  ("quality", "Quality mask on imagery", False), ("labels", "Polygons and masked pixels", False)]
OSM = "type=xyz&url=https://tile.openstreetmap.org/{z}/{x}/{y}.png&zmax=19&zmin=0"


def _hex(c):
    return "#%02x%02x%02x" % c


def _rgba(c, a=255):
    return f"{c[0]},{c[1]},{c[2]},{a}"


def _crs_xml(epsg: int) -> str:
    """CRS block in the form QGIS 3.34 writes (WKT plus authority ID)."""
    from pyproj import CRS
    c = CRS.from_epsg(epsg)
    return (f'<spatialrefsys nativeFormat="Wkt"><wkt>{html.escape(c.to_wkt())}</wkt>'
            f"<authid>EPSG:{epsg}</authid><description>{html.escape(c.name)}</description>"
            f"<geographicflag>{'true' if c.is_geographic else 'false'}</geographicflag></spatialrefsys>")


def _rgb_pipe() -> str:
    ce = lambda tag: (f"<{tag}><minValue>0</minValue><maxValue>255</maxValue>"
                      f"<algorithm>NoEnhancement</algorithm></{tag}>")
    return ('<pipe><rasterrenderer type="multibandcolor" redBand="1" greenBand="2" blueBand="3" opacity="1" '
            'alphaBand="-1">' + ce("redContrastEnhancement") + ce("greenContrastEnhancement")
            + ce("blueContrastEnhancement") + "</rasterrenderer></pipe>")


def _scl_pipe() -> str:
    entries = "".join(f'<paletteEntry value="{v}" color="{_hex(c)}" alpha="255" label="{html.escape(n)}"/>'
                      for v, n, c in SCL_CLASSES)
    return (f'<pipe><rasterrenderer type="paletted" band="1" opacity="0.8" alphaBand="-1">'
            f"<colorPalette>{entries}</colorPalette></rasterrenderer></pipe>")


def _reflectance_pipe() -> str:
    # Bands follow schema.BANDS: B02 B03 B04 ... so true colour is bands 3, 2, 1.
    ce = lambda tag: (f"<{tag}><minValue>0</minValue><maxValue>0.35</maxValue>"
                      f"<algorithm>StretchToMinimumMaximum</algorithm></{tag}>")
    return ('<pipe><rasterrenderer type="multibandcolor" redBand="3" greenBand="2" blueBand="1" opacity="1" '
            'alphaBand="-1">' + ce("redContrastEnhancement") + ce("greenContrastEnhancement")
            + ce("blueContrastEnhancement") + "</rasterrenderer></pipe>")


def _mask_status_renderer() -> str:
    cats, syms = [], []
    for i, (value, label, c) in enumerate(MASK_STATUS):
        cats.append(f'<category value="{value}" symbol="{i}" label="{html.escape(label)}" render="true"/>')
        syms.append(
            f'<symbol type="fill" name="{i}" alpha="1" clip_to_extent="1" force_rhr="0">'
            '<layer class="SimpleFill" enabled="1" locked="0" pass="0">'
            f'<Option type="Map"><Option name="color" type="QString" value="{_rgba(c, 40)}"/>'
            f'<Option name="outline_color" type="QString" value="{_rgba(c)}"/>'
            '<Option name="outline_width" type="QString" value="0.6"/>'
            '<Option name="outline_width_unit" type="QString" value="MM"/>'
            '<Option name="style" type="QString" value="solid"/></Option></layer></symbol>')
    return (f'<renderer-v2 type="categorizedSymbol" attr="mask_status" forceraster="0" symbollevels="0" enableorderby="0">'
            f'<categories>{"".join(cats)}</categories><symbols>{"".join(syms)}</symbols></renderer-v2>')


def _plain_outline_renderer() -> str:
    return ('<renderer-v2 type="singleSymbol" forceraster="0" symbollevels="0" enableorderby="0"><symbols>'
            '<symbol type="fill" name="0" alpha="1" clip_to_extent="1" force_rhr="0">'
            '<layer class="SimpleFill" enabled="1" locked="0" pass="0"><Option type="Map">'
            '<Option name="color" type="QString" value="0,0,0,0"/>'
            '<Option name="outline_color" type="QString" value="0,230,255,255"/>'
            '<Option name="outline_width" type="QString" value="0.6"/>'
            '<Option name="style" type="QString" value="no"/></Option></layer></symbol></symbols></renderer-v2>')


def _layer(kind, lid, name, source, provider, crs_epsg, body, geometry=None) -> str:
    geom = f' geometry="{geometry}"' if geometry else ""
    srs = f"<srs>{_crs_xml(crs_epsg)}</srs>" if crs_epsg else ""
    return (f'<maplayer type="{kind}"{geom} hasScaleBasedVisibilityFlag="0">'
            f"<id>{lid}</id><datasource>{html.escape(source)}</datasource>"
            f"<layername>{html.escape(name)}</layername>{srs}<provider>{provider}</provider>{body}</maplayer>")


def qml(body: str) -> str:
    """A standalone QGIS style file for one renderer."""
    return f"<!DOCTYPE qgis PUBLIC 'http://mrcc.com/qgis.dtd' 'SYSTEM'>\n<qgis version=\"3.34.0\">{body}</qgis>\n"


def write_styles(out_dir) -> list[Path]:
    out = Path(out_dir) / "styles"
    out.mkdir(parents=True, exist_ok=True)
    files = {"scl_classes.qml": qml(_scl_pipe()),
             "polygons_by_mask_status.qml": qml(_mask_status_renderer()),
             "reflectance_true_colour.qml": qml(_reflectance_pipe())}
    for name, text in files.items():
        (out / name).write_text(text)
    return [out / n for n in files]


def write_project(package_dir, scenes, title="minefoot inspection", editable=None) -> Path:
    """Write <package_dir>/project.qgs.

    `scenes` is a list of dicts with keys: name (folder under gis/), title, epsg,
    has_polygons (bool), has_mask_status (bool). Files are expected under
    gis/<name>/ as written by viewer.export_gis.

    `editable` is an optional list of vector layers to digitise into, shown at the top: dicts with
    name, source (relative path, e.g. "./labels/x.gpkg|layername=y"), epsg, colour (r, g, b) and
    form (QGIS <fieldConfiguration>/<defaults> XML, e.g. from a template's .qml).
    """
    pkg = Path(package_dir)
    write_styles(pkg)
    layers, tree = [], []
    for ed in editable or []:
        lid = f"edit_{uuid.uuid5(uuid.NAMESPACE_URL, ed['source']).hex[:12]}"
        c = ed.get("colour", (255, 0, 0))
        renderer = ('<renderer-v2 type="singleSymbol" forceraster="0" symbollevels="0" enableorderby="0"><symbols>'
                    '<symbol type="fill" name="0" alpha="1" clip_to_extent="1" force_rhr="0">'
                    '<layer class="SimpleFill" enabled="1" locked="0" pass="0"><Option type="Map">'
                    f'<Option name="color" type="QString" value="{_rgba(c, 50)}"/>'
                    f'<Option name="outline_color" type="QString" value="{_rgba(c)}"/>'
                    '<Option name="outline_width" type="QString" value="0.6"/>'
                    '<Option name="style" type="QString" value="solid"/></Option></layer></symbol></symbols></renderer-v2>')
        layers.append(_layer("vector", lid, ed["name"], ed["source"], "ogr", ed.get("epsg", 4326),
                             renderer + ed.get("form", ""), "Polygon"))
        tree.append(f'<layer-tree-layer id="{lid}" name="{html.escape(ed["name"])}" source="{html.escape(ed["source"])}" '
                    'providerKey="ogr" checked="Qt::Checked" expanded="1"/>')
    for sc in scenes:
        d = f"./gis/{sc['name']}"
        group = []
        def add(kind, key, name, source, provider, body, checked, geometry=None, crs=sc["epsg"]):
            lid = f"{key}_{uuid.uuid5(uuid.NAMESPACE_URL, source).hex[:12]}"
            layers.append(_layer(kind, lid, name, source, provider, crs, body, geometry))
            group.append(f'<layer-tree-layer id="{lid}" name="{html.escape(name)}" source="{html.escape(source)}" '
                         f'providerKey="{provider}" checked="{"Qt::Checked" if checked else "Qt::Unchecked"}" expanded="0"/>')
        if sc.get("has_polygons"):
            add("vector", "polygons", "Polygons by mask status" if sc.get("has_mask_status") else "Polygons",
                f"{d}/labels.gpkg", "ogr",
                _mask_status_renderer() if sc.get("has_mask_status") else _plain_outline_renderer(), True, "Polygon")
        add("raster", "scl", "Scene classification (SCL)", f"{d}/scl.tif", "gdal", _scl_pipe(), False)
        for key, name, checked in DISPLAY_LAYERS:
            if key == "labels" and not sc.get("has_polygons"):
                continue
            add("raster", key, name, f"{d}/{key}.tif", "gdal", _rgb_pipe(), checked)
        add("raster", "reflectance", "Reflectance, 10 bands (identify for values)", f"{d}/reflectance.tif", "gdal",
            _reflectance_pipe(), False)
        tree.append(f'<layer-tree-group name="{html.escape(sc["title"])}" checked="Qt::Checked" expanded="1">'
                    + "".join(group) + "</layer-tree-group>")
    osm_id = "osm_basemap"
    layers.append(_layer("raster", osm_id, "OpenStreetMap (internet; © OpenStreetMap contributors)", OSM, "wms", 3857,
                         "<pipe><rasterrenderer type=\"singlebandcolordata\" band=\"1\" opacity=\"1\" alphaBand=\"-1\"/></pipe>"))
    tree.append(f'<layer-tree-layer id="{osm_id}" name="OpenStreetMap" source="{html.escape(OSM)}" providerKey="wms" '
                'checked="Qt::Unchecked" expanded="0"/>')
    epsg = scenes[0]["epsg"] if scenes else 4326
    xml = ("<!DOCTYPE qgis PUBLIC 'http://mrcc.com/qgis.dtd' 'SYSTEM'>\n"
           f'<qgis projectname="{html.escape(title)}" version="3.34.0">'
           f"<homePath path=\"\"/><title>{html.escape(title)}</title>"
           f"<projectCrs>{_crs_xml(epsg)}</projectCrs>"
           f'<layer-tree-group>{"".join(tree)}</layer-tree-group>'
           f'<projectlayers>{"".join(layers)}</projectlayers>'
           '<properties><Paths><Absolute type="bool">false</Absolute></Paths>'
           # Without this QGIS 3.34 ignores projectCrs (checked by loading the project in QGIS).
           '<SpatialRefSys><ProjectionsEnabled type="int">1</ProjectionsEnabled></SpatialRefSys></properties></qgis>\n')
    out = pkg / "project.qgs"
    out.write_text(xml)
    return out
