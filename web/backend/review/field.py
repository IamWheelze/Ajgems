"""FR-095: field evidence recorded offline and synced later.

A field reviewer downloads the open field-check tasks, visits sites with no connection, and records the visit:
position, time, observer, notes and photos, plus an optional field decision. People and number plates are
blurred on the phone before a photo leaves it. The server re-encodes every photo without metadata (no EXIF
location or device serial) and serves photos only to field, GIS and admin roles. Sync is idempotent by the
visit's client UUID, so a retried upload never duplicates a visit.
"""
import hashlib
import io
import json
import uuid
from pathlib import Path

from django.conf import settings
from django.db import transaction

from audit.models import record

from .models import FieldPhoto, FieldVisit
from .services import ReviewError, submit_review

MAX_PHOTO_BYTES = 20 * 1024 * 1024
MAX_PHOTOS = 12
PHOTO_ROLES = ("field", "gis", "admin")


def tasks(user):
    from shapely.geometry import shape
    from projects.models import Detection
    out = []
    for d in (Detection.objects.filter(organisation=user.organisation, status="field_check_requested")
              .select_related("project", "scene").prefetch_related("reviews").order_by("project_id", "id")):
        last = d.reviews.order_by("-at", "-id").first()
        g = shape(json.loads(d.geometry.geojson))
        c = g.representative_point()
        out.append({"id": d.id, "project": d.project.name, "lat": round(c.y, 6), "lon": round(c.x, 6),
                    "area_ha": round(d.area_ha, 1), "score_band": d.score_band, "scene_date": d.scene.acquired_at.date(),
                    "requested_because": last.reason_code if last else "", "request_note": last.note if last else "",
                    "outline": json.loads(json.dumps(g.simplify(0.0002, preserve_topology=True).__geo_interface__)),
                    "restricted": d.restricted})
    return out


def _clean_photo(data: bytes):
    """Decode and re-encode as JPEG with no metadata. Returns (bytes, width, height)."""
    from PIL import Image, ImageOps
    if len(data) > MAX_PHOTO_BYTES:
        raise ReviewError("a photo is larger than 20 MB")
    try:
        im = Image.open(io.BytesIO(data))
        im = ImageOps.exif_transpose(im).convert("RGB")   # keep the orientation, drop everything else
    except Exception:
        raise ReviewError("a photo could not be read as an image")
    out = io.BytesIO()
    im.save(out, "JPEG", quality=88)   # no exif= argument: EXIF, GPS and maker notes are not written
    return out.getvalue(), im.width, im.height


def sync_visit(user, meta: dict, photos: list, blurred: list):
    from projects.models import Detection
    if not user.has_role("field"):
        raise PermissionError("recording field visits needs the field role")
    try:
        cid = uuid.UUID(str(meta.get("client_uuid")))
    except ValueError:
        raise ReviewError("visit needs a client_uuid")
    existing = FieldVisit.objects.filter(client_uuid=cid, organisation=user.organisation).first()
    if existing:
        return existing, False   # already synced: the phone's retry is harmless
    d = Detection.objects.get(pk=meta.get("detection"), organisation=user.organisation)
    if len(photos) > MAX_PHOTOS:
        raise ReviewError(f"at most {MAX_PHOTOS} photos per visit")
    cleaned = [_clean_photo(p) for p in photos]
    from django.utils.dateparse import parse_datetime
    visited = parse_datetime(str(meta.get("visited_at") or ""))
    if visited is None:
        raise ReviewError("visit needs visited_at (ISO date and time)")
    num = lambda k: float(meta[k]) if meta.get(k) not in (None, "") else None
    root = Path(settings.MINEFOOT["DATA_ROOT"]) / "field_photos" / str(user.organisation_id)
    root.mkdir(parents=True, exist_ok=True)
    with transaction.atomic():
        rv = None
        dec = meta.get("decision") or {}
        if dec.get("action"):
            rv = submit_review(user, d.pk, dec["action"], dec.get("reason_code", ""), dec.get("note", "") or "")
        v = FieldVisit.objects.create(organisation=user.organisation, detection=d, client_uuid=cid, observer=user,
                                      observer_name=str(meta.get("observer_name") or "")[:200], visited_at=visited,
                                      lat=num("lat"), lon=num("lon"), accuracy_m=num("accuracy_m"),
                                      notes=str(meta.get("notes") or ""), review=rv)
        for i, (data, w, h) in enumerate(cleaned):
            path = root / f"{cid}_{i}.jpg"
            path.write_bytes(data)
            FieldPhoto.objects.create(organisation=user.organisation, visit=v, path=str(path),
                                      sha256=hashlib.sha256(data).hexdigest(), width=w, height=h,
                                      blurred_regions=int(blurred[i]) if i < len(blurred) else 0)
        record(user, "field.visit", v, after={"detection": d.pk, "photos": len(cleaned), "decision": dec.get("action"),
                                              "has_position": v.lat is not None})
    return v, True


def visit_payload(v):
    return {"id": v.id, "client_uuid": str(v.client_uuid), "detection": v.detection_id, "observer": v.observer.username,
            "observer_name": v.observer_name, "visited_at": v.visited_at, "lat": v.lat, "lon": v.lon,
            "accuracy_m": v.accuracy_m, "notes": v.notes, "synced_at": v.synced_at,
            "decision": {"action": v.review.action, "reason_code": v.review.reason_code} if v.review else None,
            "photos": [{"id": p.id, "width": p.width, "height": p.height, "blurred_regions": p.blurred_regions, "sha256": p.sha256}
                       for p in v.photos.all()]}
