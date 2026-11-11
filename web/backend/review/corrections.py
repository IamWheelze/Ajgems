"""FR-096 correction requests on released results.

Anyone who can see a released candidate (customer viewers included) can ask for it to be corrected. An
administrator gives the request an owner (a GIS reviewer or administrator). The owner accepts or declines it
with a written outcome. Accepting can apply the fix through the normal, append-only review rules (reject or
edit) or restrict the location (FR-005). Every step is an immutable event and an audit record.
"""
from django.db import transaction
from django.utils import timezone

from audit.models import record

from .models import CORRECTION_KINDS, CorrectionEvent, CorrectionRequest
from .services import ReviewError, submit_review

OPEN = ("open", "in_review")


def _event(req, user, action, note=""):
    CorrectionEvent.objects.create(request=req, actor=user, action=action, state_after=req.state, note=note)
    record(user, f"correction.{action}", req, after={"state": req.state, "owner": req.owner.username if req.owner else None,
                                                     "note": note[:500], "detection": req.detection_id})


def create(user, detection, kind, description, contact=""):
    if kind not in CORRECTION_KINDS:
        raise ReviewError(f"kind must be one of {CORRECTION_KINDS}")
    if not (description or "").strip():
        raise ReviewError("describe what is wrong")
    if detection.status != "released":
        raise ReviewError("corrections are for released results; unreleased candidates are still in review")
    with transaction.atomic():
        req = CorrectionRequest.objects.create(organisation=user.organisation, detection=detection, kind=kind,
                                               description=description.strip(), contact=contact[:200], requested_by=user)
        _event(req, user, "opened", description.strip())
    return req


def assign(user, req_id, owner):
    if not user.has_role("admin"):
        raise PermissionError("an administrator assigns the owner")
    if owner.organisation_id != user.organisation_id or not owner.has_role("gis", "admin"):
        raise ReviewError("the owner must be a GIS reviewer or administrator in your organisation")
    with transaction.atomic():
        req = CorrectionRequest.objects.select_for_update().get(pk=req_id, organisation=user.organisation)
        if req.state not in OPEN:
            raise ReviewError(f"request is {req.state}")
        req.owner, req.state = owner, "in_review"
        req.save(update_fields=["owner", "state", "updated_at"])
        _event(req, user, "assigned", f"owner: {owner.username}")
    return req


def resolve(user, req_id, decision, outcome, apply=None):
    """decision accept|decline, with the outcome written for the requester. apply (accept only): {action: reject|edit,
    reason_code, note?, geometry?} through the review rules, or {action: restrict} for a sensitive location."""
    if decision not in ("accept", "decline"):
        raise ReviewError("decision must be accept or decline")
    if not (outcome or "").strip():
        raise ReviewError("write the outcome for the requester")
    with transaction.atomic():
        req = CorrectionRequest.objects.select_for_update().select_related("detection").get(pk=req_id, organisation=user.organisation)
        if req.state != "in_review":
            raise ReviewError("assign an owner first" if req.state == "open" else f"request is {req.state}")
        if user.pk != req.owner_id and not user.has_role("admin"):
            raise PermissionError("only the owner or an administrator resolves a request")
        if decision == "accept" and apply:
            act = apply.get("action")
            if act == "restrict":
                req.detection.restricted = True
                req.detection.save(update_fields=["restricted"])
                record(user, "detection.restrict", req.detection, after={"restricted": True, "reason": f"correction {req.pk}"})
            elif act in ("reject", "edit"):
                req.review = submit_review(user, req.detection_id, act, apply.get("reason_code", ""),
                                           apply.get("note", "") or f"correction request {req.pk}", apply.get("geometry"))
            else:
                raise ReviewError("apply.action must be reject, edit or restrict")
        req.state, req.outcome = ("accepted" if decision == "accept" else "declined"), outcome.strip()
        req.save(update_fields=["state", "outcome", "review", "updated_at"])
        _event(req, user, decision + "ed" if decision == "accept" else "declined", outcome.strip())
    return req


def withdraw(user, req_id, note=""):
    with transaction.atomic():
        req = CorrectionRequest.objects.select_for_update().get(pk=req_id, organisation=user.organisation)
        if req.requested_by_id != user.pk:
            raise PermissionError("only the requester can withdraw a request")
        if req.state not in OPEN:
            raise ReviewError(f"request is {req.state}")
        req.state = "withdrawn"
        req.save(update_fields=["state", "updated_at"])
        _event(req, user, "withdrawn", note)
    return req


def payload(req):
    return {"id": req.id, "detection": req.detection_id, "project": req.detection.project_id, "kind": req.kind,
            "description": req.description, "contact": req.contact, "requested_by": req.requested_by.username,
            "owner": req.owner.username if req.owner else None, "state": req.state, "outcome": req.outcome,
            "review": req.review_id, "created_at": req.created_at, "updated_at": req.updated_at,
            "events": [{"action": e.action, "state_after": e.state_after, "actor": e.actor.username, "note": e.note, "at": e.at}
                       for e in req.events.all()]}
