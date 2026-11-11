"""FR-086 cost, compute and storage per job, and FR-087 per-organisation quotas.

Estimates calibrate themselves: time and storage per tile are the medians of the organisation's last
finished jobs (at least three), else the configured defaults. Actual values are measured when a job ends.
Money is shown only when rates are configured; with none set, jobs run on the organisation's own CPU and
Sentinel-2 imagery is free, so cost is reported as compute time and storage.
"""
from datetime import timezone as dt_tz
from pathlib import Path
from statistics import median

from django.conf import settings
from django.utils import timezone

DEFAULT_BYTES_PER_TILE = 25e6   # measured 2026-11-11: 16 to 22 MB per 10 km tile (prepared imagery, score map, polygons)


def _dir_bytes(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) if path.exists() else 0


def job_dir(job) -> Path:
    return Path(settings.MINEFOOT["JOB_ROOT"]) / f"job_{job.pk}"


def money(seconds, storage_bytes):
    cfg = settings.MINEFOOT
    rate_cpu, rate_gb = cfg["CPU_COST_PER_HOUR"], cfg["STORAGE_COST_PER_GB_MONTH"]
    if not rate_cpu and not rate_gb:
        return None
    return {"compute": round(seconds / 3600 * rate_cpu, 4), "storage_per_month": round(storage_bytes / 1e9 * rate_gb, 4),
            "currency": cfg["COST_CURRENCY"]}


def calibration(org):
    """Seconds and bytes per processed tile from the organisation's last finished jobs."""
    from .models import Job
    jobs = [j for j in Job.objects.filter(organisation=org, kind="detection", state__in=("succeeded", "succeeded_with_warnings"))
            .order_by("-finished_at")[:10] if j.actual.get("tiles_processed")]
    if len(jobs) < 3:
        return {"seconds_per_tile": settings.MINEFOOT["SECONDS_PER_TILE"], "bytes_per_tile": DEFAULT_BYTES_PER_TILE,
                "basis": f"defaults (fewer than 3 finished jobs measured)"}
    return {"seconds_per_tile": round(median(j.actual["seconds"] / j.actual["tiles_processed"] for j in jobs), 1),
            "bytes_per_tile": round(median(j.actual["storage_bytes"] / j.actual["tiles_processed"] for j in jobs)),
            "basis": f"median of the last {len(jobs)} finished jobs"}


def estimate(org, tiles):
    c = calibration(org)
    secs, b = tiles * c["seconds_per_tile"], tiles * c["bytes_per_tile"]
    return {"seconds": round(secs), "storage_bytes": round(b), "money": money(secs, b), **c}


def record_actual(job):
    """Measure a finished job: wall time, tiles processed, bytes kept on disk, and money if rates are set."""
    tiles = job.tiles.exclude(state="skipped").exclude(prepared_path="").count()
    secs = (job.finished_at - job.started_at).total_seconds() if job.started_at and job.finished_at else 0.0
    b = _dir_bytes(job_dir(job))
    est = job.estimate.get("cost") or {}
    job.actual = {"seconds": round(secs, 1), "tiles_processed": tiles, "storage_bytes": b, "money": money(secs, b),
                  "seconds_vs_estimate": round(secs / est["seconds"], 2) if est.get("seconds") else None}
    job.save(update_fields=["actual"])
    return job.actual


class QuotaExceeded(ValueError):
    pass


def month_start():
    n = timezone.now()
    return n.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def usage(org):
    from .models import Job
    started = Job.objects.filter(organisation=org, started_at__gte=month_start()).select_related("area")
    return {"km2_this_month": round(sum(j.area.area_km2 for j in started if j.area), 1), "jobs_this_month": started.count(),
            "storage_gb": round(sum(_dir_bytes(job_dir(j)) for j in Job.objects.filter(organisation=org)) / 1e9, 3)}


def check_quota(org, area_km2, extra_bytes=0):
    """FR-087: refuse a job that would take the organisation over a quota, saying which and by how much."""
    u = usage(org)
    problems = []
    if org.quota_km2_per_month is not None and u["km2_this_month"] + area_km2 > org.quota_km2_per_month:
        problems.append(f"area: {u['km2_this_month']:,.0f} km² used this month + {area_km2:,.0f} km² for this job is over the "
                        f"{org.quota_km2_per_month:,.0f} km² monthly quota")
    if org.quota_jobs_per_month is not None and u["jobs_this_month"] + 1 > org.quota_jobs_per_month:
        problems.append(f"jobs: {u['jobs_this_month']} started this month; the quota is {org.quota_jobs_per_month}")
    if org.quota_storage_gb is not None and u["storage_gb"] + extra_bytes / 1e9 > org.quota_storage_gb:
        problems.append(f"storage: {u['storage_gb']:.2f} GB kept + about {extra_bytes / 1e9:.2f} GB for this job is over the "
                        f"{org.quota_storage_gb:g} GB quota; remove old job data or ask an administrator")
    if problems:
        raise QuotaExceeded("organisation quota exceeded (FR-087): " + "; ".join(problems))
    return u
