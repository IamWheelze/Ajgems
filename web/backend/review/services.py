"""Submit a review: checks role, reason and allowed transition, then appends review and audit rows."""
import json

from django.contrib.gis.geos import GEOSGeometry
from django.db import transaction

from audit.models import record
from projects.models import Detection

from .models import ACTIONS, EVIDENCE_GRADE, REASONS, Review


class ReviewError(ValueError):
    """A review that breaks a rule. The message names the rule."""


def submit_review(user, detection_id, action, reason_code, note="", geometry=None) -> Review:
    if action not in ACTIONS:
        raise ReviewError(f"unknown action {action!r}")
    status_after, roles, allowed_from = ACTIONS[action]
    role = next((r for r in user.roles or [] if r in roles), None)
    if role is None:
        raise PermissionError(f"{action} needs one of the roles {sorted(roles)}")
    if reason_code not in REASONS[action]:
        raise ReviewError(f"{action} needs a reason code from {REASONS[action]}")
    if reason_code == "other" and not note.strip():
        raise ReviewError("reason 'other' needs a note")
    if action == "edit" and geometry is None:
        raise ReviewError("edit needs the new geometry")
    if action != "edit" and geometry is not None:
        raise ReviewError("only edit may change the geometry")
    with transaction.atomic():
        d = (Detection.objects.select_for_update()
             .get(pk=detection_id, organisation=user.organisation))   # other organisations: DoesNotExist
        if d.project.status == "archived":
            raise ReviewError("project is archived and read-only")
        if allowed_from is not None and d.status not in allowed_from:
            raise ReviewError(f"{action} is only allowed from {sorted(allowed_from)}, not {d.status}")
        new_geom = None
        if geometry is not None:
            new_geom = GEOSGeometry(geometry if isinstance(geometry, str) else json.dumps(geometry), srid=4326)
            if new_geom.geom_type != "Polygon":
                raise ReviewError("edited geometry must be a polygon")
            if not new_geom.valid:
                raise ReviewError(f"edited outline is not valid: {new_geom.valid_reason}. Move the vertex so the outline "
                                  "does not cross itself.")
        rv = Review.objects.create(
            organisation=user.organisation, detection=d, action=action, reason_code=reason_code, note=note,
            reviewer=user, reviewer_role=role, status_before=d.status, status_after=status_after,
            geometry_before=d.geometry if new_geom is not None else None, geometry_after=new_geom)
        before = {"status": d.status, "evidence_grade": d.evidence_grade}
        d.status = status_after
        if new_geom is not None:
            d.geometry = new_geom
        grade = EVIDENCE_GRADE.get(action)
        if grade and grade > d.evidence_grade:
            d.evidence_grade = grade
        d.save(update_fields=["status", "geometry", "evidence_grade"])
        record(user, f"review.{action}", d, before=before,
               after={"status": d.status, "evidence_grade": d.evidence_grade, "reason": reason_code, "review": rv.pk})
    return rv


def add_missed_site(user, project_id, geometry, reason_code, note=""):
    """FR-093: record a disturbance the model missed, as a false negative of the latest finished job there."""
    from shapely.geometry import shape

    from projects.aoi import geodesic_km2
    from projects.exports import selected_jobs
    from projects.models import Project

    from .models import MISSED_REASONS, MissedSite
    if not user.has_role("gis"):
        raise PermissionError("adding a missed site needs the gis role")
    if reason_code not in MISSED_REASONS:
        raise ReviewError(f"a missed site needs a reason code from {MISSED_REASONS}")
    if reason_code == "other" and not note.strip():
        raise ReviewError("reason 'other' needs a note")
    p = Project.objects.get(pk=project_id, organisation=user.organisation)   # other organisations: DoesNotExist
    if p.status == "archived":
        raise ReviewError("project is archived and read-only")
    try:
        g = GEOSGeometry(geometry if isinstance(geometry, str) else json.dumps(geometry), srid=4326)
    except (ValueError, TypeError) as exc:
        raise ReviewError(f"the outline could not be read: {exc}")
    if g.geom_type != "Polygon":
        raise ReviewError("a missed site must be one polygon")
    if not g.valid:
        raise ReviewError(f"outline is not valid: {g.valid_reason}. Move the vertex so the outline does not cross itself.")
    jobs = [j for j in selected_jobs(p) if j.area is not None and j.area.geometry.intersects(g)]
    if not jobs:
        raise ReviewError("draw the missed site inside an area with a finished detection job")
    job = max(jobs, key=lambda j: (j.finished_at or j.created_at, j.pk))
    tile = job.tiles.filter(geometry__intersects=g.point_on_surface).select_related("scene").first()
    with transaction.atomic():
        ms = MissedSite.objects.create(
            organisation=user.organisation, project=p, job=job, geometry=g,
            area_ha=round(geodesic_km2(shape(json.loads(g.geojson))) * 100, 3), reason_code=reason_code, note=note,
            tile_outcome=tile.outcome if tile else "", scene_date=tile.scene.acquired_at.date() if tile and tile.scene else None,
            added_by=user, added_by_role="gis")
        record(user, "review.missed_site", ms, after={"job": job.pk, "area_ha": ms.area_ha, "reason": reason_code,
                                                      "tile_outcome": ms.tile_outcome})
    return ms


def missed_feature(ms):
    return {"type": "Feature", "geometry": json.loads(ms.geometry.geojson),
            "properties": {"id": ms.id, "job": ms.job_id, "area_ha": ms.area_ha, "reason_code": ms.reason_code, "note": ms.note,
                           "tile_outcome": ms.tile_outcome, "scene_date": ms.scene_date, "added_by": ms.added_by.username,
                           "at": ms.at, "counts_as": "false negative" if ms.tile_outcome != "insufficient_usable_imagery"
                           else "not a model miss: no usable imagery there (FR-052)"}}
