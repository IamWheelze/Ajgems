"""Detection job runner (FR-080 to FR-085). One Celery task per job, tile by tile.

Each tile is processed independently and its state is saved, so a resumed job does not
repeat finished tiles. Every tile ends with one of the four FR-052 outcomes: a tile
whose imagery fails the quality check is "insufficient usable imagery", never
"no candidate found". Candidates crossing tile edges are merged at the end.
"""
import json
import logging
import traceback
from pathlib import Path

from celery import shared_task
from django.conf import settings
from django.contrib.gis.geos import GEOSGeometry, Polygon
from django.db import transaction
from django.utils import timezone

from audit.models import record

from . import pipeline
from .models import Detection, Job, Scene, Tile

log = logging.getLogger(__name__)
FAILURE_REASONS = {  # FRD 9.4
    "source": "source unavailable", "quality": "insufficient usable imagery", "schema": "model schema mismatch",
    "memory": "out of memory", "storage": "storage error", "invalid": "invalid input", "worker": "worker lost",
}


class Cancelled(Exception):
    pass


def _set(job, **fields):
    for k, v in fields.items():
        setattr(job, k, v)
    job.save(update_fields=list(fields))


def _check_cancel(job):
    job.refresh_from_db(fields=["state"])
    if job.state == Job.State.CANCELLING:
        raise Cancelled()


def _scene_for(provider, item):
    p = item["properties"]
    from datetime import datetime
    from shapely.geometry import shape
    return Scene.objects.get_or_create(
        provider=provider, source_id=item["id"],
        defaults=dict(sensor=p.get("platform", ""), processing_baseline=str(p.get("s2:processing_baseline", "")),
                      acquired_at=datetime.fromisoformat(p["datetime"].replace("Z", "+00:00")),
                      cloud_pct=p.get("eo:cloud_cover"),
                      licence_note=f"Contains modified Copernicus Sentinel data {p['datetime'][:4]}",
                      footprint=GEOSGeometry(shape(item["geometry"]).envelope.wkt, srid=4326)))[0]


def process_tile(job, tile, items, provider, work):
    """Fetch, check, score and vectorise one tile. Returns its candidate polygons (GeoDataFrame) or None."""
    from shapely import wkt
    from minefoot.quality import coverage
    from minefoot.vectorise import vectorise
    import rasterio
    tg = wkt.loads(tile.geometry.wkt)
    item, cover = pipeline.choose_scene(items, tg)
    if item is None or cover < 0.01:
        tile.state, tile.outcome, tile.failure_reason = Tile.State.SKIPPED, Tile.Outcome.NOT_PROCESSED, "no scene covers this tile"
        tile.save(); return None
    scene = _scene_for(provider, item)
    tile.scene, tile.scene_cover, tile.state = scene, cover, Tile.State.FETCHING
    tile.save(update_fields=["scene", "scene_cover", "state"])
    _set(job, state=Job.State.FETCHING)
    prep = pipeline.fetch_tile(provider, item, list(tg.bounds), work, tile.grid_id)
    tile.prepared_path = str(prep); tile.save(update_fields=["prepared_path"])
    tile.state = Tile.State.CHECKING; tile.save(update_fields=["state"])
    _set(job, state=Job.State.CHECKING)
    with rasterio.open(prep / "scl.tif") as s:
        cov = coverage(s.read(1))
    tile.usable_pct, tile.quality_decision = cov.usable_fraction * 100, cov.decision
    if cov.decision == "block":
        tile.state, tile.outcome = Tile.State.DONE, Tile.Outcome.INSUFFICIENT
        tile.save(); return None
    tile.state = Tile.State.RUNNING; tile.save(update_fields=["state", "usable_pct", "quality_decision"])
    _set(job, state=Job.State.RUNNING)
    score = pipeline.score_prepared(prep, job.parameters.get("model_run"))   # the model frozen at start
    score_path = pipeline.write_score(score, prep, Path(work) / f"{tile.grid_id}_score.tif")
    threshold = job.parameters["threshold"]
    g = vectorise(score_path, threshold, job.parameters["mmu_ha"], prepared_dir=prep)
    area = wkt.loads(job.area.geometry.wkt)
    if len(g):
        g["geometry"] = g.geometry.intersection(area).intersection(tg)
        g = g[~g.geometry.is_empty & g.geometry.geom_type.isin(["Polygon", "MultiPolygon"])].explode(index_parts=False)
        g = g[g.geometry.geom_type == "Polygon"]
        g["scene_pk"] = scene.pk
        g["tile"] = tile.grid_id
    tile.candidates = int(len(g))
    tile.outcome = Tile.Outcome.CANDIDATE_FOUND if len(g) else Tile.Outcome.NO_CANDIDATE
    tile.state = Tile.State.DONE
    tile.save()
    if len(g):
        g.to_file(Path(work) / f"{tile.grid_id}_candidates.gpkg", driver="GPKG")
    return g


def merge_candidates(job, work):
    """Join candidates that touch across tile edges, then save them as detections (FR-062)."""
    import geopandas as gpd
    import pandas as pd
    from shapely.ops import unary_union
    from minefoot.vectorise import score_band
    parts = [gpd.read_file(p) for p in sorted(Path(work).glob("*_candidates.gpkg"))]
    if not parts:
        return 0
    g = gpd.GeoDataFrame(pd.concat(parts, ignore_index=True), crs=4326)
    eq = g.to_crs(6933)   # equal-area, for area weights
    g["w"] = eq.area
    merged = unary_union(list(eq.geometry.buffer(0.5)))   # touch across a tile edge -> one polygon
    geoms = list(merged.geoms) if merged.geom_type == "MultiPolygon" else [merged]
    merged_eq = gpd.GeoDataFrame(geometry=geoms, crs=6933)
    joined = gpd.sjoin(eq.assign(i=range(len(eq))), merged_eq.reset_index(names="m"), predicate="intersects")
    threshold, mmu = job.parameters["threshold"], job.parameters["mmu_ha"]
    n = 0
    with transaction.atomic():
        Detection.objects.filter(job=job).delete()   # rebuilt on resume; no reviews exist before success
        for m, grp in joined.groupby("m"):
            rows = g.iloc[grp["i"].values]
            w = rows["w"].values
            geom = merged_eq.geometry.iloc[m].buffer(-0.5)
            area_ha = geom.area / 1e4
            if area_ha < mmu or geom.is_empty:
                continue
            geom4326 = gpd.GeoSeries([geom], crs=6933).to_crs(4326).iloc[0]
            if geom4326.geom_type == "MultiPolygon":
                geom4326 = max(geom4326.geoms, key=lambda p: p.area)
            mean = float((rows["score_mean"].values * w).sum() / w.sum())
            masked = float((rows["masked_fraction"].values * w).sum() / w.sum())
            status = "mostly_masked" if masked >= 0.5 else "partly_masked" if masked >= 0.05 else "assessed"
            scene_pk = int(rows.loc[rows["w"].idxmax(), "scene_pk"])
            gg = GEOSGeometry(geom4326.wkt, srid=4326)
            Detection.objects.create(
                organisation=job.organisation, project=job.project, job=job, scene_id=scene_pk, geometry=gg,
                original_geometry=gg, area_ha=area_ha, score_mean=mean, score_band=score_band(mean, threshold),
                threshold=threshold, min_mapping_unit_ha=mmu, model_version=job.model_version,
                input_schema_version="s2-l2a-10band-v1", masked_fraction=masked, mask_status=status)
            n += 1
    return n


@shared_task(bind=True)
def run_detection_job(self, job_id):
    job = Job.objects.select_related("area", "project").get(pk=job_id)
    work = Path(settings.MINEFOOT["JOB_ROOT"]) / f"job_{job.pk}"
    work.mkdir(parents=True, exist_ok=True)
    provider, items = job.parameters["provider"], job.parameters["items"]
    if not job.started_at:
        _set(job, started_at=timezone.now())
    try:
        for tile in job.tiles.exclude(state__in=[Tile.State.DONE, Tile.State.SKIPPED]).order_by("grid_id"):
            _check_cancel(job)
            try:
                process_tile(job, tile, items, provider, work)
            except Cancelled:
                raise
            except MemoryError:
                tile.state, tile.failure_reason = Tile.State.FAILED, FAILURE_REASONS["memory"]; tile.save()
            except Exception as exc:   # one bad tile does not stop the job
                log.warning("tile %s failed: %s", tile.grid_id, exc)
                reason = FAILURE_REASONS["source"] if "CURL" in str(exc) or "HTTP" in str(exc) else f"error: {exc}"[:80]
                tile.state, tile.failure_reason = Tile.State.FAILED, reason; tile.save()
        _check_cancel(job)
        _set(job, state=Job.State.VECTORISING)
        n = merge_candidates(job, work)
        tiles = list(job.tiles.all())
        failed = sum(t.state == Tile.State.FAILED for t in tiles)
        insufficient = sum(t.outcome == Tile.Outcome.INSUFFICIENT for t in tiles)
        warned = sum(t.quality_decision == "warn" for t in tiles)
        if failed == len(tiles):
            state, msg = Job.State.FAILED, "every tile failed; see tile reasons"
        elif insufficient == len(tiles):
            state, msg = Job.State.BLOCKED_BY_QUALITY, "no tile had enough usable imagery"
        elif failed or insufficient or warned:
            state, msg = Job.State.SUCCEEDED_WITH_WARNINGS, (f"{n} candidates; {failed} tile(s) failed, {insufficient} with "
                                                             f"insufficient usable imagery, {warned} with low coverage")
        else:
            state, msg = Job.State.SUCCEEDED, f"{n} candidates"
        _set(job, state=state, message=msg, finished_at=timezone.now(),
             failure_reason=FAILURE_REASONS["quality"] if state == Job.State.BLOCKED_BY_QUALITY else "")
        record(job.created_by, "job.finished", job, after={"state": state, "candidates": n})
    except Cancelled:
        _set(job, state=Job.State.CANCELLED, finished_at=timezone.now(), message="cancelled; finished tiles are kept")
        record(job.created_by, "job.cancelled", job)
    except Exception as exc:
        _set(job, state=Job.State.RESUMABLE, message=f"stopped: {exc}"[:500], failure_reason=FAILURE_REASONS["worker"])
        log.error("job %s stopped: %s", job.pk, traceback.format_exc())
        raise
