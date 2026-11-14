"""Export packages (FR-106 to FR-109): data, PDF report, licence notice and checksummed manifest.

One zip per request. Fields follow FRD 9.2. The report always contains the coverage
map and the limits section (FR-107): they cannot be switched off. Every package
carries the Copernicus credit for each scene year (FR-108) and a manifest with a
SHA-256 for every file, signed with Ed25519 (FR-109, see signing.py).
"""
from __future__ import annotations
import csv
import hashlib
import io
import json
import zipfile
from collections import Counter
from datetime import datetime, timezone

from .serializers import SCORE_STATEMENT

FORMATS = ("geojson", "gpkg", "csv")
AREA_METHOD = "geodesic area on the WGS 84 ellipsoid (pyproj.Geod)"


def detection_record(d) -> dict:
    """FRD 9.2 exported fields for one detection."""
    from projects.aoi import geodesic_km2
    from shapely import wkt
    last = d.reviews.order_by("-at", "-id").first()
    s = d.scene
    return {
        "project_id": d.project_id, "detection_id": d.id, "crs": "EPSG:4326",
        "area_ha": round(geodesic_km2(wkt.loads(d.geometry.wkt)) * 100, 3), "area_method": AREA_METHOD,
        "imagery_start": s.acquired_at.date().isoformat(), "imagery_end": s.acquired_at.date().isoformat(),
        "scene_ids": s.source_id, "sensor": s.sensor, "processing_baseline": s.processing_baseline,
        "usable_coverage_pct": round(100 * (1 - d.masked_fraction), 1), "mask_status": d.mask_status,
        "model_version": d.model_version, "input_schema_version": d.input_schema_version,
        "threshold": round(d.threshold, 3), "min_mapping_unit_ha": d.min_mapping_unit_ha,
        "score_mean": round(d.score_mean, 2), "score_band": d.score_band, "score_type": d.score_type,
        "review_status": d.status, "review_reason": last.reason_code if last else "",
        "reviewer_role": last.reviewer_role if last else "", "reviewed_at": last.at.isoformat() if last else "",
        "evidence_grade": d.evidence_grade, "evidence_notes": last.note if last else "",
        "processed_at": d.created_at.isoformat(), "statement": SCORE_STATEMENT,
    }


def _geojson(dets, recs) -> bytes:
    feats = [{"type": "Feature", "geometry": json.loads(d.geometry.geojson), "properties": r} for d, r in zip(dets, recs)]
    return json.dumps({"type": "FeatureCollection", "features": feats}, indent=1).encode()


def _csv(dets, recs) -> bytes:
    buf = io.StringIO()
    cols = list(recs[0]) + ["geometry_wkt_epsg4326"] if recs else ["detection_id", "geometry_wkt_epsg4326"]
    w = csv.DictWriter(buf, fieldnames=cols)
    w.writeheader()
    for d, r in zip(dets, recs):
        w.writerow({**r, "geometry_wkt_epsg4326": d.geometry.wkt})
    return buf.getvalue().encode()


def _gpkg(dets, recs) -> bytes:
    import tempfile
    from pathlib import Path
    import geopandas as gpd
    from shapely import wkt
    g = gpd.GeoDataFrame(recs, geometry=[wkt.loads(d.geometry.wkt) for d in dets], crs=4326)
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "candidates.gpkg"
        g.to_file(p, layer="candidate_footprints", driver="GPKG")
        return p.read_bytes()


def licence_text(project, dets) -> str:
    years = sorted({d.scene.acquired_at.year for d in dets})
    credits = "\n".join(f"  Contains modified Copernicus Sentinel data {y}" for y in years) or "  (no imagery in this export)"
    from registry.models import ModelVersion
    names = sorted({str(d.model_version).split(":")[0] for d in dets})
    attrib = sorted({l["attribution"] for mv in ModelVersion.objects.filter(name__in=names)
                     for l in (mv.licences or []) if l.get("attribution")})
    model_credits = ("Model components:\n" + "\n".join(f"  {a}" for a in attrib) + "\n\n") if attrib else ""
    return (f"Licence and attribution notice\n{'=' * 30}\n\nProject: {project.name}\n\n"
            f"Imagery (Copernicus Sentinel-2, free, full and open under the Sentinel Data Legal Notice):\n{credits}\n\n"
            f"{model_credits}"
            "Candidate footprints were produced by an automated model and reviewed by people as recorded in each row.\n"
            f"{SCORE_STATEMENT}\n\n"
            "Base maps are not included. If OpenStreetMap is shown alongside, credit: (c) OpenStreetMap contributors.\n")


def _coverage_map_png(project, dets, jobs, size=900) -> bytes:
    """Tiles by FR-052 outcome (insufficient hatched) and candidate outlines, in a simple lon/lat frame."""
    from PIL import Image, ImageDraw
    from shapely import wkt
    geoms = [wkt.loads(t.geometry.wkt) for j in jobs for t in j.tiles.all()] + [wkt.loads(d.geometry.wkt) for d in dets]
    geoms += [wkt.loads(a.geometry.wkt) for a in project.areas.all()]
    img = Image.new("RGB", (size, size), "white")
    d = ImageDraw.Draw(img)
    if not geoms:
        d.text((20, 20), "No areas, tiles or candidates", fill="black")
        return _png(img)
    xs = [c[0] for g in geoms for c in (g.exterior.coords if g.geom_type == "Polygon" else [p for gg in g.geoms for p in gg.exterior.coords])]
    ys = [c[1] for g in geoms for c in (g.exterior.coords if g.geom_type == "Polygon" else [p for gg in g.geoms for p in gg.exterior.coords])]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    span = max(x1 - x0, y1 - y0) or 1e-6
    pad = 30
    f = (size - 2 * pad) / span
    to = lambda c: (pad + (c[0] - x0) * f, size - pad - (c[1] - y0) * f)
    def polys(g):
        return [g] if g.geom_type == "Polygon" else list(g.geoms)
    colours = {"candidate_found": (230, 159, 0), "no_candidate_found_in_usable_imagery": (86, 180, 233),
               "insufficient_usable_imagery": (153, 153, 153), "not_processed": (85, 85, 85)}
    for j in jobs:
        for t in j.tiles.all():
            for p in polys(wkt.loads(t.geometry.wkt)):
                pts = [to(c) for c in p.exterior.coords]
                col = colours.get(t.outcome, (85, 85, 85))
                d.polygon(pts, fill=tuple(int(c * 0.35 + 255 * 0.65) for c in col), outline=col)
                if t.outcome == "insufficient_usable_imagery":   # hatch: not assessed is never blank
                    bx0, by0 = min(p[0] for p in pts), min(p[1] for p in pts)
                    bx1, by1 = max(p[0] for p in pts), max(p[1] for p in pts)
                    k = bx0 - (by1 - by0)
                    while k < bx1:
                        d.line([(k, by1), (k + (by1 - by0), by0)], fill=(110, 110, 110), width=1)
                        k += 8
    for det in dets:
        for p in polys(wkt.loads(det.geometry.wkt)):
            d.line([to(c) for c in p.exterior.coords], fill=(150, 30, 0), width=2)
    for a in project.areas.all():   # drawn last so tiles never hide the area outline
        for p in polys(wkt.loads(a.geometry.wkt)):
            d.line([to(c) for c in p.exterior.coords], fill=(0, 90, 160), width=3)
    return _png(img)


def _png(img) -> bytes:
    b = io.BytesIO(); img.save(b, "PNG"); return b.getvalue()


def report_pdf(project, dets, recs, jobs, user, statuses) -> bytes:
    """FR-107: maps, method, coverage, limits, versions and evidence grades. Coverage map and limits always included."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas
    from projects.pipeline import model_card
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4, pageCompression=1)
    W, H = A4
    y = [H - 2 * cm]

    def line(text, size=10, bold=False, gap=0.5):
        if y[0] < 2.5 * cm:
            c.showPage(); y[0] = H - 2 * cm
        c.setFont("Helvetica-Bold" if bold else "Helvetica", size)
        for chunk in _wrap(text, 95 if size <= 10 else 70):
            c.drawString(2 * cm, y[0], chunk); y[0] -= gap * cm
        y[0] -= 0.1 * cm

    now = datetime.now(timezone.utc)
    line(f"Candidate footprint report: {project.name}", 15, True, 0.7)
    line(f"Generated {now:%Y-%m-%d %H:%M} UTC by a {', '.join(user.roles)} user. Status filter: {', '.join(statuses)}.", 9)
    line("Purpose", 12, True); line(project.purpose)
    line("Coverage map", 12, True)
    png = _coverage_map_png(project, dets, jobs)
    side = 13 * cm
    if y[0] - side < 2 * cm:
        c.showPage(); y[0] = H - 2 * cm
    c.drawImage(ImageReader(io.BytesIO(png)), 2 * cm, y[0] - side, side, side)
    y[0] -= side + 0.3 * cm
    line("Blue: areas of interest. Tiles: orange candidate found, light blue no candidate found in usable imagery, "
         "hatched grey insufficient usable imagery (not assessed), dark grey not processed. Dark red outlines: candidates.", 8)
    tiles = Counter(t.outcome for j in jobs for t in j.tiles.all())
    line("Coverage", 12, True)
    line(f"Tiles: {sum(tiles.values())} in {len(jobs)} job(s). Candidate found {tiles.get('candidate_found', 0)}; "
         f"no candidate found in usable imagery {tiles.get('no_candidate_found_in_usable_imagery', 0)}; "
         f"insufficient usable imagery {tiles.get('insufficient_usable_imagery', 0)}; not processed {tiles.get('not_processed', 0)}.")
    line("Areas marked insufficient or not processed were NOT assessed. Absence of candidates there means nothing.")
    if recs:
        masked = [r for r in recs if r["mask_status"] != "assessed"]
        line(f"{len(masked)} of {len(recs)} candidates had part of their area removed by the cloud and quality mask.")
    line("Method", 12, True)
    line("Sentinel-2 L2A surface reflectance (10 bands at 10 m) from a STAC catalogue, converted with the processing-baseline "
         "offset, masked with the scene classification layer, scored by a U-Net segmentation model, thresholded, and "
         "converted to polygons with a minimum mapping unit. Each candidate was then reviewed by people.")
    card = model_card()
    line("Limits", 12, True)
    for t in (SCORE_STATEMENT, "Scores are model scores, not calibrated probabilities. Score bands are for display only.",
              card["domain_warning"] or "The model has been tested on locked Nigerian sites; see the G3 report for its limits.",
              "Candidates are visible land disturbance resembling surface mining on the stated image date. Quarries, borrow "
              "pits, bare fields, riverbanks and dry ponds can look similar.",
              "Bright mine ground can be classed as cloud by the scene classification and left unassessed.",
              "Areas are geodesic; boundaries follow 10 m pixels."):
        line(f"- {t}", 9)
    line("Versions", 12, True)
    versions = sorted({(r["model_version"], r["input_schema_version"], r["threshold"]) for r in recs})
    for mv, sv, th in versions or [(card["model_version"], "s2-l2a-10band-v1", card["threshold"])]:
        line(f"Model {mv}; input schema {sv}; threshold {th}; minimum mapping unit {recs[0]['min_mapping_unit_ha'] if recs else 1.0} ha.", 9)
    line("Results", 12, True)
    by_status = Counter(r["review_status"] for r in recs)
    line(f"{len(recs)} candidates exported. By review status: " + (", ".join(f"{k} {v}" for k, v in sorted(by_status.items())) or "none") + ".")
    grades = Counter(r["evidence_grade"] for r in recs)
    line("Evidence grades: " + (", ".join(f"{g} {grades.get(g, 0)}" for g in ("E0", "E1", "E2", "E3"))) +
         ". E0 model only; E1 analyst review on the same imagery; E2 independent dated reference imagery; E3 field or operator verification.")
    line("Attribution", 12, True)
    for l in licence_text(project, dets).splitlines()[3:]:
        if l.strip():
            line(l.strip(), 8, gap=0.4)
    c.save()
    return buf.getvalue()


def _wrap(text, width):
    words, lines, cur = str(text).split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width:
            lines.append(cur); cur = w
        else:
            cur = f"{cur} {w}".strip()
    return lines + [cur] if cur else lines or [""]


def selected_jobs(project, job_id=None):
    """The job asked for, or by default the latest finished job for each area, so repeated runs over the
    same area are not exported twice."""
    finished = ("succeeded", "succeeded_with_warnings")
    if job_id is not None:
        return list(project.jobs.filter(pk=job_id))
    latest = {}
    for j in project.jobs.filter(state__in=finished).order_by("created_at", "id"):
        latest[j.area_id] = j
    return list(latest.values())


def build_package(project, dets, user, fmt, statuses, jobs=None, restricted_withheld=0) -> tuple[bytes, dict]:
    if fmt not in FORMATS:
        raise ValueError(f"format must be one of {FORMATS}")
    dets = list(dets)
    recs = [detection_record(d) for d in dets]
    jobs = jobs if jobs is not None else selected_jobs(project)
    files = {f"candidates.{fmt}": {"geojson": _geojson, "gpkg": _gpkg, "csv": _csv}[fmt](dets, recs),
             "report.pdf": report_pdf(project, dets, recs, jobs, user, statuses),
             "LICENCE_AND_ATTRIBUTION.txt": licence_text(project, dets).encode()}
    from review.models import MissedSite
    from review.services import missed_feature
    missed = list(MissedSite.objects.filter(project=project, job__in=jobs).select_related("added_by"))
    if missed:   # FR-093: analyst-added misses are a separate layer, never mixed with model candidates
        files["missed_sites.geojson"] = json.dumps({"type": "FeatureCollection", "features": [missed_feature(m) for m in missed]},
                                                   default=str, indent=1).encode()
    manifest = {"project_id": project.id, "project": project.name, "created_at": datetime.now(timezone.utc).isoformat(),
                "format": fmt, "status_filter": statuses, "candidates": len(dets), "missed_sites": len(missed), "restricted_withheld": restricted_withheld, "jobs": [j.pk for j in jobs],
                "files": {n: {"sha256": hashlib.sha256(b).hexdigest(), "bytes": len(b)} for n, b in files.items()}}
    from .signing import VERIFIER, how_to_verify, public_info, sign
    manifest = sign(manifest)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for n, b in files.items():
            z.writestr(n, b)
        z.writestr("manifest.json", json.dumps(manifest, indent=2))
        z.writestr("verify_export.py", VERIFIER.read_bytes())
        z.writestr("HOW_TO_VERIFY.txt", how_to_verify(public_info()))
    return buf.getvalue(), manifest
