"""FR-075 annotation pool: the only route from reviewed output to training labels.

A GIS reviewer nominates a reviewed detection (or a missed site). It becomes a training label only when
an independent person accepts it: not the nominator, and not anyone who reviewed that detection or added
that missed site. Nominations and decisions are append-only; the latest decision is the state.
Nothing inside the Nigerian test regions may enter the pool, so the locked test set stays independent (ML-07).
"""
import json

from django.db import transaction

from audit.models import record

from .models import NEGATIVE_FROM, NEGATIVE_TYPE, POSITIVE_FROM, QC_DECISIONS, MissedSite, Nomination, QCDecision, Review
from .services import ReviewError


def state(nom) -> str:
    last = nom.decisions.order_by("-at", "-id").first()
    return "nominated" if last is None else {"accept": "accepted", "return": "returned"}[last.decision]


def _test_region(geom):
    """Name of the Nigerian test region this geometry touches, if any (regardless of lock state)."""
    from pathlib import Path

    import yaml
    from django.conf import settings
    from shapely import wkt
    from shapely.geometry import box
    cfg = Path(settings.MINEFOOT["NIGERIA_AOIS"])
    if not cfg.exists():
        return None
    g = wkt.loads(geom.wkt)
    for r in yaml.safe_load(cfg.read_text()).get("regions", []):
        if g.intersects(box(*r["bbox"])):
            return r["name"]
    return None


def nominate(user, detection_id=None, missed_site_id=None, note=""):
    from projects.models import Detection
    if not user.has_role("gis"):
        raise PermissionError("nominating needs the gis role")
    if (detection_id is None) == (missed_site_id is None):
        raise ReviewError("nominate one detection or one missed site")
    with transaction.atomic():
        if detection_id is not None:
            d = Detection.objects.select_for_update().select_related("scene").get(pk=detection_id, organisation=user.organisation)
            src = {"detection": d}
            if d.status in POSITIVE_FROM:
                kind, ntype = "mining", ""
            elif d.status in NEGATIVE_FROM:
                last = d.reviews.order_by("-at", "-id").first()
                ntype = NEGATIVE_TYPE.get(last.reason_code if last else "")
                if not ntype:
                    raise ReviewError(f"rejected for '{last.reason_code if last else '?'}', which names no hard-negative type; "
                                      f"only {sorted(NEGATIVE_TYPE)} can become negatives")
                kind = "hard_negative"
            else:
                raise ReviewError(f"only confirmed, edited, field-confirmed or rejected candidates can be nominated, not {d.status}")
            geom, project, scene, date, status = d.geometry, d.project, d.scene.source_id, d.scene.acquired_at.date(), d.status
            active = Nomination.objects.filter(detection=d)
        else:
            m = MissedSite.objects.select_related("job").get(pk=missed_site_id, organisation=user.organisation)
            if m.tile_outcome == "insufficient_usable_imagery":
                raise ReviewError("this missed site lies where the imagery was unusable, so it cannot be checked on the scene")
            src = {"missed_site": m}
            tile = m.job.tiles.filter(geometry__intersects=m.geometry.point_on_surface).select_related("scene").first()
            kind, ntype, geom, project, status = "mining", "", m.geometry, m.project, "missed_site"
            scene, date = (tile.scene.source_id, tile.scene.acquired_at.date()) if tile and tile.scene else ("", m.scene_date)
            active = Nomination.objects.filter(missed_site=m)
        if any(state(n) != "returned" for n in active):
            raise ReviewError("already in the annotation pool")
        region = _test_region(geom)
        if region:
            raise ReviewError(f"inside the Nigerian test region '{region}': kept out of training so the test set stays "
                              "independent (ML-07)")
        if project.status == "archived":
            raise ReviewError("project is archived and read-only")
        nom = Nomination.objects.create(organisation=user.organisation, project=project, kind=kind, negative_type=ntype,
                                        geometry=geom, scene_id=scene, scene_date=date, status_at_nomination=status,
                                        nominated_by=user, note=note, **src)
        record(user, "pool.nominate", nom, after={"kind": kind, "negative_type": ntype, **{k: v.pk for k, v in src.items()}})
    return nom


def involved(nom) -> set:
    """Users who may not check this nomination: its nominator and everyone who reviewed or added the source."""
    ids = {nom.nominated_by_id}
    if nom.detection_id:
        ids |= set(Review.objects.filter(detection_id=nom.detection_id).values_list("reviewer_id", flat=True))
    if nom.missed_site_id:
        ids.add(nom.missed_site.added_by_id)
    return ids


def decide(user, nomination_id, decision, reason_code, note=""):
    if not user.has_role("ml", "gis"):
        raise PermissionError("QC needs the ml or gis role")
    if decision not in QC_DECISIONS:
        raise ReviewError(f"decision must be one of {sorted(QC_DECISIONS)}")
    if reason_code not in QC_DECISIONS[decision]:
        raise ReviewError(f"{decision} needs a reason code from {QC_DECISIONS[decision]}")
    if reason_code == "other" and not note.strip():
        raise ReviewError("reason 'other' needs a note")
    with transaction.atomic():
        nom = Nomination.objects.select_for_update(of=("self",)).select_related("missed_site").get(pk=nomination_id, organisation=user.organisation)
        if user.pk in involved(nom):
            raise PermissionError("independent QC: you nominated this example or reviewed its source, so someone else must check it")
        if state(nom) != "nominated":
            raise ReviewError(f"already {state(nom)}")
        role = "ml" if user.has_role("ml") else "gis"
        qc = QCDecision.objects.create(organisation=user.organisation, nomination=nom, decision=decision,
                                       reason_code=reason_code, note=note, checker=user, checker_role=role)
        record(user, f"pool.{decision}", nom, after={"reason": reason_code, "qc": qc.pk})
    return qc


def feature(nom, user=None):
    last = nom.decisions.order_by("-at", "-id").first()
    return {"type": "Feature", "geometry": json.loads(nom.geometry.geojson), "properties": {
        "id": nom.id, "project": nom.project_id, "detection": nom.detection_id, "missed_site": nom.missed_site_id,
        "kind": nom.kind, "negative_type": nom.negative_type, "scene_id": nom.scene_id, "scene_date": nom.scene_date,
        "status_at_nomination": nom.status_at_nomination, "protocol_version": nom.protocol_version,
        "nominated_by": nom.nominated_by.username, "nominated_at": nom.at, "note": nom.note, "state": state(nom),
        "decision": None if last is None else {"decision": last.decision, "reason_code": last.reason_code, "note": last.note,
                                               "checker": last.checker.username, "at": last.at},
        "can_check": None if user is None else (user.pk not in involved(nom) and state(nom) == "nominated"
                                                and user.has_role("ml", "gis"))}}


def export_accepted(user):
    """Accepted examples only, as GeoJSON plus a manifest with SHA-256 (the dataset adapter reads this)."""
    import hashlib
    from datetime import datetime, timezone
    if not user.has_role("ml"):
        raise PermissionError("exporting training labels needs the ml role")
    noms = [n for n in Nomination.objects.filter(organisation=user.organisation).select_related("nominated_by")
            if state(n) == "accepted"]
    fc = {"type": "FeatureCollection", "features": []}
    for n in noms:
        f = feature(n)
        p = f["properties"]
        f["properties"] = {"example_id": f"pool-{n.id}", "label_class": "mining" if n.kind == "mining" else "hard_negative",
                           "negative_type": p["negative_type"], "scene_id": p["scene_id"], "scene_date": p["scene_date"],
                           "protocol_version": p["protocol_version"], "source": "reviewed detection" if n.detection_id else "missed site",
                           "qc_reason": p["decision"]["reason_code"], "qc_at": p["decision"]["at"],
                           "independent_qc": True}
        fc["features"].append(f)
    body = json.dumps(fc, default=str, indent=1).encode()
    manifest = {"created_at": datetime.now(timezone.utc).isoformat(), "examples": len(noms),
                "mining": sum(n.kind == "mining" for n in noms), "hard_negative": sum(n.kind == "hard_negative" for n in noms),
                "negative_types": {t: sum(n.negative_type == t for n in noms) for t in sorted({n.negative_type for n in noms} - {""})},
                "sha256": hashlib.sha256(body).hexdigest(),
                "note": "Licence: derived from Copernicus Sentinel data and the organisation's own reviews. Not in any locked test set."}
    record(user, "pool.export", None, after={k: manifest[k] for k in ("examples", "sha256")})
    return body, manifest
