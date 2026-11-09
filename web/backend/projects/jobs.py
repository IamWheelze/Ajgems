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
    provider, items = pipeline.find_scenes(bbox, start, end, max_cloud)
    if not items:
        raise JobError(f"no scene under {max_cloud}% cloud in {start} to {end}; widen the window or the cloud limit")
    tiles = pipeline.tile_grid(area.geometry, settings.MINEFOOT["TILE_KM"])
    card = pipeline.model_card()
    covered = sum(1 for _, t in tiles if pipeline.choose_scene(items, t)[1] > 0.01)
    est = {"tiles": len(tiles), "tiles_with_imagery": covered, "scenes_found": len(items),
           "clearest_scene": items[0]["id"], "clearest_cloud_pct": items[0]["properties"].get("eo:cloud_cover"),
           "estimated_minutes": round(len(tiles) * settings.MINEFOOT["SECONDS_PER_TILE"] / 60, 1),
           "estimate_basis": f"{settings.MINEFOOT['SECONDS_PER_TILE']:.0f} s per {settings.MINEFOOT['TILE_KM']:.0f} km tile on CPU",
           "area_km2": area.area_km2, "model": card}
    with transaction.atomic():
        job = Job.objects.create(
            organisation=project.organisation, project=project, area=area, kind="detection",
            state=Job.State.ESTIMATED, model_version=card["model_version"], created_by=user, estimate=est,
            parameters={"start": start, "end": end, "max_cloud": max_cloud, "threshold": card["threshold"],
                        "mmu_ha": settings.MINEFOOT["MMU_HA"], "tile_km": settings.MINEFOOT["TILE_KM"],
                        "provider": provider, "items": items, "model_run": str(settings.MINEFOOT["MODEL_RUN"])})
        Tile.objects.bulk_create([Tile(job=job, grid_id=gid, geometry=GEOSGeometry(t.wkt, srid=4326)) for gid, t in tiles])
        record(user, "job.estimate", job, after={k: v for k, v in est.items() if k != "model"})
    return job


def start(user, job) -> Job:
    """Estimated -> queued. Parameters are frozen from here on."""
    if job.state != Job.State.ESTIMATED:
        raise JobError(f"only an estimated job can start, this one is {job.state}")
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
