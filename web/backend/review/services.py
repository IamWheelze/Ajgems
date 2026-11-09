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
