"""FR-076 annotation on a job tile: polygon, brush, eraser, split, merge, delete, undo, and mask export.

Geometry is edited in the tile's UTM zone (metres), so a brush radius is a real distance, then stored in
EPSG:4326 as a list of separate polygons per class (split pieces share an edge, which a single MultiPolygon
may not). Every edit is checked with shapely and repaired with make_valid, so a
saved version is always valid; each edit appends an immutable version. The mask is rasterised on the tile's own
10 m grid (its prepared reflectance), so it lines up pixel for pixel with the imagery the model sees.
Model candidates are not shown while annotating, so labels are not biased towards the model.
"""
import json
from pathlib import Path

from django.db import transaction
from shapely import make_valid
from shapely.geometry import LineString, MultiPolygon, Polygon, mapping, shape
from shapely.ops import split, transform, unary_union

from audit.models import record

from .models import LABEL_CLASSES, AnnotationTask, AnnotationVersion
from .services import ReviewError


def _proj(task):
    """(to_utm, to_wgs84, utm_crs) for the tile, from its prepared imagery."""
    import rasterio
    from pyproj import Transformer
    with rasterio.open(Path(task.tile.prepared_path) / "reflectance.tif") as s:
        crs = s.crs
    fwd = Transformer.from_crs("EPSG:4326", crs, always_xy=True).transform
    inv = Transformer.from_crs(crs, "EPSG:4326", always_xy=True).transform
    return fwd, inv, crs


def _polys(g):
    g = make_valid(g)
    parts = [g] if g.geom_type == "Polygon" else [p for p in getattr(g, "geoms", []) if p.geom_type == "Polygon"]
    parts += [q for p in getattr(g, "geoms", []) if p.geom_type == "MultiPolygon" for q in p.geoms]
    return [p for p in parts if not p.is_empty and p.area > 1.0]   # drop slivers under 1 m²


def _join(parts, new):
    """Union a new shape with only the parts it touches, leaving other parts (e.g. split pieces) separate."""
    touched = [p for p in parts if p.intersects(new)]
    return [p for p in parts if not p.intersects(new)] + _polys(unary_union(touched + [new]))


def current(task):
    v = task.versions.order_by("-at", "-id").first()
    return v.labels if v else {}


def features(task, labels=None):
    labels = current(task) if labels is None else labels
    out = []
    for cls, polys in labels.items():
        for i, gj in enumerate(polys or []):
            out.append({"type": "Feature", "geometry": gj, "properties": {"class": cls, "index": i}})
    return {"type": "FeatureCollection", "features": out}


def edit(user, task_id, op, cls, data):
    """Apply one edit and append a version. Coordinates arrive in WGS 84 (lon, lat)."""
    if cls not in LABEL_CLASSES:
        raise ReviewError(f"class must be one of {LABEL_CLASSES}")
    with transaction.atomic():
        task = AnnotationTask.objects.select_for_update().select_related("tile").get(pk=task_id, organisation=user.organisation)
        if task.state != "open":
            raise ReviewError("this task is done; reopen it to edit")
        if op == "undo":
            vs = list(task.versions.order_by("-at", "-id")[:2])
            if not vs:
                raise ReviewError("nothing to undo")
            labels = vs[1].labels if len(vs) > 1 else {}
            AnnotationVersion.objects.create(task=task, labels=labels, op="undo", editor=user)
            return features(task, labels)
        fwd, inv, _ = _proj(task)
        labels = current(task)
        tile = transform(fwd, shape(json.loads(task.tile.geometry.geojson)))
        parts = [transform(fwd, shape(g)) for g in labels.get(cls) or []]
        coords = data.get("coordinates") or []
        to_utm = lambda pts: [fwd(x, y) for x, y in pts]
        if op == "polygon":
            if len(coords) < 3:
                raise ReviewError("a polygon needs at least three points")
            new = unary_union(_polys(Polygon(to_utm(coords))))
            parts = _join(parts, new)
        elif op in ("brush", "erase"):
            r = float(data.get("radius_m", 15))
            if not 2 <= r <= 500:
                raise ReviewError("brush radius must be 2 to 500 m")
            stroke = (LineString(to_utm(coords)) if len(coords) > 1 else shape({"type": "Point", "coordinates": to_utm(coords)[0]})).buffer(r)
            parts = _join(parts, stroke) if op == "brush" else [q for p in parts for q in _polys(p.difference(stroke))]
        elif op == "split":
            if len(coords) < 2:
                raise ReviewError("draw a line across the polygon to split it")
            line = LineString(to_utm(coords))
            hit = [i for i, p in enumerate(parts) if p.intersects(line)]
            if not hit:
                raise ReviewError("the line does not cross a polygon of this class")
            out = []
            for i, p in enumerate(parts):
                out += [q for q in split(p, line).geoms if q.geom_type == "Polygon"] if i in hit else [p]
            if len(out) == len(parts):
                raise ReviewError("the line must cross the polygon from edge to edge")
            parts = [q for p in out for q in _polys(p)]
        elif op == "merge":
            idx = [int(i) for i in data.get("indices", [])]
            if len(idx) < 2 or any(i >= len(parts) for i in idx):
                raise ReviewError("choose two or more polygons of this class to merge")
            merged = unary_union([parts[i].buffer(0.01) for i in idx]).buffer(-0.01)
            if merged.geom_type != "Polygon":
                raise ReviewError("those polygons do not touch, so they cannot be merged into one")
            parts = [p for i, p in enumerate(parts) if i not in idx] + [merged]
        elif op == "delete":
            i = int(data.get("index", -1))
            if not 0 <= i < len(parts):
                raise ReviewError("no such polygon")
            parts = parts[:i] + parts[i + 1:]
        else:
            raise ReviewError("unknown edit")
        parts = [q for p in parts for q in _polys(p.intersection(tile))]   # labels stay inside the tile
        labels = {**labels, cls: [mapping(transform(inv, p)) for p in parts] if parts else None}
        labels = {k: v for k, v in labels.items() if v}
        AnnotationVersion.objects.create(task=task, labels=labels, op=op, editor=user)
    return features(task, labels)


def export_mask(task):
    """GeoTIFF on the tile's grid: 0 unlabelled, 1 mining, 2.. hard-negative classes (LABEL_CLASSES order); plus GeoJSON."""
    import rasterio
    from rasterio.features import rasterize
    fwd, _, _ = _proj(task)
    labels = current(task)
    with rasterio.open(Path(task.tile.prepared_path) / "reflectance.tif") as s:
        prof, shape_ = s.profile, (s.height, s.width)
        tr = s.transform
    shapes = []
    for k, cls in enumerate(LABEL_CLASSES[::-1]):   # mining last, so it wins where classes overlap
        for gj in labels.get(cls) or []:
            g = transform(fwd, shape(gj))
            assert g.is_valid, "stored labels must be valid"
            shapes.append((g, LABEL_CLASSES.index(cls) + 1))
    mask = rasterize(shapes, out_shape=shape_, transform=tr, fill=0, dtype="uint8") if shapes else None
    import io
    import numpy as np
    buf = io.BytesIO()
    p = {**prof, "count": 1, "dtype": "uint8", "nodata": None, "compress": "deflate", "driver": "GTiff"}
    with rasterio.io.MemoryFile() as mf:
        with mf.open(**p) as d:
            d.write(mask if mask is not None else np.zeros(shape_, "uint8"), 1)
            d.update_tags(classes=json.dumps({str(i + 1): c for i, c in enumerate(LABEL_CLASSES)}),
                          protocol_version=task.protocol_version, scene=task.tile.scene.source_id if task.tile.scene else "")
        buf.write(mf.read())
    fc = features(task, labels)
    for f in fc["features"]:
        f["properties"].update({"protocol_version": task.protocol_version, "task": task.pk})
    return buf.getvalue(), json.dumps(fc).encode(), {"pixels": {c: int((mask == i + 1).sum()) if mask is not None else 0
                                                                for i, c in enumerate(LABEL_CLASSES)}}


def create(user, tile_id):
    from projects.models import Tile
    if not user.has_role("gis", "ml"):
        raise PermissionError("annotation needs the gis or ml role")
    t = Tile.objects.select_related("job__project", "scene").get(pk=tile_id, job__organisation=user.organisation)
    if not t.prepared_path or not (Path(t.prepared_path) / "reflectance.tif").exists():
        raise ReviewError("this tile has no stored imagery to annotate")
    task = AnnotationTask.objects.create(organisation=user.organisation, project=t.job.project, tile=t, created_by=user)
    record(user, "annotation.create", task, after={"tile": t.grid_id, "job": t.job_id})
    return task


def payload(task):
    t = task.tile
    return {"id": task.id, "project": task.project_id, "job": t.job_id, "tile": t.grid_id, "tile_id": t.id,
            "scene": t.scene.source_id if t.scene else None, "date": t.scene.acquired_at.date() if t.scene else None,
            "state": task.state, "protocol_version": task.protocol_version, "created_by": task.created_by.username,
            "versions": task.versions.count(), "bounds": list(json.loads(t.geometry.geojson) and t.geometry.extent)}
