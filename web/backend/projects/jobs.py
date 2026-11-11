"""Job estimate, start, cancel and resume (FR-013, FR-080, FR-085). Views call these."""
from datetime import date

from django.conf import settings
from django.contrib.gis.geos import GEOSGeometry
from django.db import transaction

from audit.models import record

from . import pipeline
from .models import AreaOfInterest, Job, Tile


class JobError(ValueError):
    pass


def estimate(user, project, area_id, start, end, max_cloud=20.0) -> Job:
    """Draft -> estimated: search scenes, lay the tile grid, estimate time. Nothing is downloaded yet."""
    if project.status != "active":
        raise JobError("project is archived and read-only")
    try:
        area = AreaOfInterest.objects.get(pk=area_id, project=project)
    except AreaOfInterest.DoesNotExist:
        raise JobError("choose one of this project's areas")
    try:
        d0, d1 = date.fromisoformat(start), date.fromisoformat(end)
    except (TypeError, ValueError):
        raise JobError("dates must be YYYY-MM-DD")
    if d1 < d0 or (d1 - d0).days > 366:
        raise JobError("the date window must run forwards and be at most a year")
    reason = pipeline.embargoed(area.geometry)
    if reason:
        raise JobError(reason)
    bbox = list(area.geometry.extent)
    allowed, refused = pipeline.allowed_providers(project.intended_use)
    if not allowed:
        raise JobError(f"no imagery source is approved for {project.intended_use.replace('_', ' ')} use (FR-025): "
                       + "; ".join(f"{p}: {why}" for p, why in refused.items()))
    try:
        provider, items = pipeline.find_scenes(bbox, start, end, max_cloud, providers=allowed)
    except RuntimeError as exc:
        raise RuntimeError(f"all approved catalogues failed ({', '.join(allowed)}): {exc}")
    if not items:
        raise JobError(f"no scene under {max_cloud}% cloud in {start} to {end}; widen the window or the cloud limit")
    tiles = pipeline.tile_grid(area.geometry, settings.MINEFOOT["TILE_KM"])
    try:
        card = pipeline.model_card()
    except ValueError as exc:   # schema mismatch (FR-102)
        raise JobError(str(exc))
    covered = sum(1 for _, t in tiles if pipeline.choose_scene(items, t)[1] > 0.01)
    est = {"tiles": len(tiles), "tiles_with_imagery": covered, "scenes_found": len(items),
           "clearest_scene": items[0]["id"], "clearest_cloud_pct": items[0]["properties"].get("eo:cloud_cover"),
           "estimated_minutes": round(len(tiles) * settings.MINEFOOT["SECONDS_PER_TILE"] / 60, 1),
           "estimate_basis": f"{settings.MINEFOOT['SECONDS_PER_TILE']:.0f} s per {settings.MINEFOOT['TILE_KM']:.0f} km tile on CPU",
           "area_km2": area.area_km2, "model": card, "domain": pipeline.domain_check(area.geometry, start, end), "source": provider, "sources_tried_in_order": allowed,
           "sources_not_approved": refused}
    with transaction.atomic():
        job = Job.objects.create(
            organisation=project.organisation, project=project, area=area, kind="detection",
            state=Job.State.ESTIMATED, model_version=card["model_version"], created_by=user, estimate=est,
            parameters={"start": start, "end": end, "max_cloud": max_cloud, "threshold": card["threshold"],
                        "mmu_ha": settings.MINEFOOT["MMU_HA"], "tile_km": settings.MINEFOOT["TILE_KM"],
                        "provider": provider, "items": items, "model_run": card.get("model_run", str(settings.MINEFOOT["MODEL_RUN"]))})
        Tile.objects.bulk_create([Tile(job=job, grid_id=gid, geometry=GEOSGeometry(t.wkt, srid=4326)) for gid, t in tiles])
        record(user, "job.estimate", job, after={k: v for k, v in est.items() if k != "model"})
    return job


def start(user, job) -> Job:
    """Estimated -> queued. Parameters are frozen from here on."""
    if job.state != Job.State.ESTIMATED:
        raise JobError(f"only an estimated job can start, this one is {job.state}")
    allowed, _ = pipeline.allowed_providers(job.project.intended_use)
    if job.parameters.get("provider") not in allowed:   # an approval withdrawn after the estimate
        raise JobError(f"source {job.parameters.get('provider')} is no longer approved for "
                       f"{job.project.intended_use.replace('_', ' ')} use (FR-025); estimate again")
    from .tasks import run_detection_job
    job.state = Job.State.QUEUED
    job.save(update_fields=["state"])
    record(user, "job.start", job)
    transaction.on_commit(lambda: run_detection_job.delay(job.pk))
    return job


def cancel(user, job) -> Job:
    if job.state in (Job.State.ESTIMATED, Job.State.RESUMABLE):
        job.state = Job.State.CANCELLED
    elif job.state in (Job.State.QUEUED, Job.State.FETCHING, Job.State.CHECKING, Job.State.RUNNING, Job.State.VECTORISING):
        job.state = Job.State.CANCELLING   # the worker stops before its next tile
    else:
        raise JobError(f"a {job.state} job cannot be cancelled")
    job.save(update_fields=["state"])
    record(user, "job.cancel", job, after={"state": job.state})
    return job


def resume(user, job) -> Job:
    """Resumable or cancelled -> queued. Finished tiles are not repeated (FR-085)."""
    if job.state not in (Job.State.RESUMABLE, Job.State.CANCELLED):
        raise JobError(f"only a stopped or cancelled job can resume, this one is {job.state}")
    if job.started_at is None:
        raise JobError("this job never started; start it instead")
    from .tasks import run_detection_job
    job.state = Job.State.QUEUED
    job.save(update_fields=["state"])
    record(user, "job.resume", job, after={"tiles_done": job.tiles.filter(state="done").count()})
    transaction.on_commit(lambda: run_detection_job.delay(job.pk))
    return job


FINISHED = (Job.State.SUCCEEDED, Job.State.SUCCEEDED_WITH_WARNINGS)


def _settings(threshold, mmu_ha):
    try:
        t, m = float(threshold), float(mmu_ha)
    except (TypeError, ValueError):
        raise JobError("threshold and minimum size must be numbers")
    if not (0.01 <= t <= 0.99) or not (0.1 <= m <= 1000):
        raise JobError("threshold must be 0.01 to 0.99 and minimum size 0.1 to 1,000 ha")
    return t, m


def _scored_tiles(job):
    from pathlib import Path
    tiles = [t for t in job.tiles.select_related("scene").order_by("grid_id")
             if t.outcome in (Tile.Outcome.CANDIDATE_FOUND, Tile.Outcome.NO_CANDIDATE)
             and t.score_path and Path(t.score_path).exists() and t.prepared_path]
    if not tiles:
        raise JobError("this job has no stored score maps, so it cannot be re-thresholded")
    return tiles


def preview(job, threshold, mmu_ha) -> dict:
    """FR-126: the job's candidates at other settings, from its stored score maps, without saving anything.
    Uses the job's own vectorise and merge code, so at the job's settings it reproduces the job's result."""
    from shapely.geometry import shape
    from .tasks import merge_parts, tile_candidates
    if job.state not in FINISHED:
        raise JobError("only a finished job can be previewed")
    t, m = _settings(threshold, mmu_ha)
    tiles = _scored_tiles(job)
    cands = merge_parts([tile_candidates(x, job.area.geometry, t, m) for x in tiles], t, m)
    import json
    current = [(d.pk, shape(json.loads(d.original_geometry.geojson))) for d in job.detections.all()]
    feats = []
    for c in cands:
        hit = any(c["geometry"].intersects(g) for _, g in current)
        feats.append({"type": "Feature", "geometry": c["geometry"].__geo_interface__,
                      "properties": {"change": "kept" if hit else "added", "area_ha": round(c["area_ha"], 2),
                                     "score_band": c["score_band"], "mask_status": c["mask_status"]}})
    for pk, g in current:
        if not any(g.intersects(c["geometry"]) for c in cands):
            feats.append({"type": "Feature", "geometry": g.__geo_interface__,
                          "properties": {"change": "removed", "detection": pk}})
    count = lambda k: sum(f["properties"]["change"] == k for f in feats)
    return {"type": "FeatureCollection", "features": feats,
            "settings": {"threshold": t, "mmu_ha": m},
            "job_settings": {"threshold": job.parameters["threshold"], "mmu_ha": job.parameters["mmu_ha"]},
            "summary": {"candidates": len(cands), "kept": count("kept"), "added": count("added"), "removed": count("removed"),
                        "area_ha": round(sum(c["area_ha"] for c in cands), 1),
                        "job_candidates": len(current)}}


def rethreshold(user, job, threshold, mmu_ha) -> Job:
    """Save new settings as a new job derived from this one (same scenes and score maps). The original job,
    its candidates and their reviews are kept unchanged; the new job becomes the area's latest result."""
    from django.utils import timezone
    from .tasks import merge_parts, tile_candidates
    if job.state not in FINISHED:
        raise JobError("only a finished job can be re-thresholded")
    if job.project.status != "active":
        raise JobError("project is archived and read-only")
    t, m = _settings(threshold, mmu_ha)
    tiles = _scored_tiles(job)
    per_tile = {x.pk: tile_candidates(x, job.area.geometry, t, m) for x in tiles}
    cands = merge_parts(list(per_tile.values()), t, m)
    now = timezone.now()
    with transaction.atomic():
        new = Job.objects.create(
            organisation=job.organisation, project=job.project, area=job.area, kind="rethreshold", state=job.state,
            model_version=job.model_version, created_by=user, estimate=job.estimate, started_at=now, finished_at=now,
            parameters={**job.parameters, "threshold": t, "mmu_ha": m, "derived_from": job.pk},
            message=f"re-thresholded from job {job.pk}: {len(cands)} candidates at threshold {t:.2f}, minimum {m:g} ha")
        new.scenes.set(job.scenes.all())
        for x in job.tiles.all():
            g = per_tile.get(x.pk)
            outcome = x.outcome if g is None else (Tile.Outcome.CANDIDATE_FOUND if len(g) else Tile.Outcome.NO_CANDIDATE)
            Tile.objects.create(job=new, grid_id=x.grid_id, geometry=x.geometry, state=x.state, scene=x.scene,
                                scene_cover=x.scene_cover, usable_pct=x.usable_pct, quality_decision=x.quality_decision,
                                candidates=0 if g is None else len(g), failure_reason=x.failure_reason,
                                prepared_path=x.prepared_path, score_path=x.score_path, outcome=outcome)
        from .models import Detection
        for c in cands:
            gg = GEOSGeometry(c["geometry"].wkt, srid=4326)
            Detection.objects.create(
                organisation=job.organisation, project=job.project, job=new, scene_id=c["scene_pk"], geometry=gg,
                original_geometry=gg, area_ha=c["area_ha"], score_mean=c["score_mean"], score_band=c["score_band"],
                threshold=t, min_mapping_unit_ha=m, model_version=job.model_version,
                input_schema_version="s2-l2a-10band-v1", masked_fraction=c["masked_fraction"], mask_status=c["mask_status"])
        record(user, "job.rethreshold", new, before={"job": job.pk, **{k: job.parameters[k] for k in ("threshold", "mmu_ha")}},
               after={"threshold": t, "mmu_ha": m, "candidates": len(cands)})
    return new
