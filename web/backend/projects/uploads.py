"""FR-022 resumable uploads of Sentinel-2 L2A products.

A client creates an upload with the file's size, then sends chunks in order. Each chunk carries its byte range
and its SHA-256; the server appends it only if it starts exactly where the file ends and the hash matches, so an
interrupted transfer resumes from the last verified byte and a corrupted chunk is never kept. When the last byte
arrives, the whole file's SHA-256 is recorded and the product is validated with minefoot.upload.read_safe.
"""
import hashlib
import os
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.contrib.gis.geos import Polygon
from django.db import transaction

from audit.models import record

from .models import Upload


class UploadError(ValueError):
    pass


class OffsetMismatch(UploadError):
    def __init__(self, received):
        super().__init__(f"chunk must start at byte {received}")
        self.received = received


def create(user, project, filename, size):
    if project.status != "active":
        raise UploadError("project is archived and read-only")
    try:
        size = int(size)
    except (TypeError, ValueError):
        raise UploadError("give the file size in bytes")
    if not 0 < size <= settings.MINEFOOT["MAX_UPLOAD_BYTES"]:
        raise UploadError(f"size must be 1 byte to {settings.MINEFOOT['MAX_UPLOAD_BYTES'] / 1e9:g} GB")
    name = Path(str(filename or "")).name
    if not name.lower().endswith(".zip"):
        raise UploadError("upload a zipped Sentinel-2 L2A .SAFE folder (.zip)")
    root = Path(settings.MINEFOOT["UPLOAD_ROOT"]); root.mkdir(parents=True, exist_ok=True)
    u = Upload.objects.create(organisation=user.organisation, project=project, filename=name, size=size, created_by=user, path="")
    u.path = str(root / f"upload_{u.pk}.zip")
    Path(u.path).write_bytes(b"")
    u.save(update_fields=["path"])
    record(user, "upload.create", u, after={"filename": name, "size": size})
    return u


def append(user, upload_id, start, data: bytes, chunk_sha256: str):
    with transaction.atomic():
        u = Upload.objects.select_for_update().get(pk=upload_id, organisation=user.organisation)
        if u.state != Upload.State.RECEIVING:
            raise UploadError(f"upload is {u.state}")
        if start != u.received:
            raise OffsetMismatch(u.received)
        if not data:
            raise UploadError("empty chunk")
        if u.received + len(data) > u.size:
            raise UploadError("chunk runs past the declared file size")
        if hashlib.sha256(data).hexdigest() != (chunk_sha256 or "").lower():
            raise UploadError("chunk checksum does not match; send it again")
        with open(u.path, "r+b") as f:   # write at the verified offset; a partial earlier write is overwritten
            f.seek(u.received); f.write(data); f.truncate(); f.flush(); os.fsync(f.fileno())
        u.received += len(data)
        u.save(update_fields=["received"])
    if u.received == u.size:
        finish(user, u)
    return u


def finish(user, u):
    from minefoot.upload import NotCompatible, read_safe
    h = hashlib.sha256()
    with open(u.path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    u.sha256 = h.hexdigest()
    try:
        meta = read_safe(u.path)
        w, s, e, n = meta["bbox_wgs84"]
        u.meta, u.cloud_pct, u.state = meta, meta["cloud_pct"], Upload.State.READY
        u.footprint = Polygon.from_bbox((w, s, e, n)); u.footprint.srid = 4326
        u.acquired_at = datetime.fromisoformat(meta["datetime"].replace("Z", "+00:00"))
    except (NotCompatible, OSError, KeyError, ValueError) as exc:
        u.state, u.error = Upload.State.FAILED, str(exc)[:500]
    u.save()
    record(user, "upload.complete", u, after={"state": u.state, "sha256": u.sha256, "error": u.error,
                                              "scene_id": u.meta.get("scene_id")})
    return u


def payload(u):
    return {"id": u.id, "project": u.project_id, "filename": u.filename, "size": u.size, "received": u.received,
            "state": u.state, "sha256": u.sha256, "error": u.error, "scene_id": u.meta.get("scene_id"),
            "acquired_at": u.acquired_at, "cloud_pct": u.cloud_pct, "processing_baseline": u.meta.get("processing_baseline"),
            "chunk_bytes": settings.MINEFOOT["UPLOAD_CHUNK_BYTES"], "created_at": u.created_at}


def items_for(org, bbox, start, end, max_cloud):
    """Ready uploads covering the area in the window, as STAC item dicts for the job (provider 'upload')."""
    from minefoot.upload import stac_item
    box = Polygon.from_bbox(tuple(bbox)); box.srid = 4326
    qs = Upload.objects.filter(organisation=org, state=Upload.State.READY, footprint__intersects=box,
                               acquired_at__date__gte=start, acquired_at__date__lte=end)
    return [stac_item(u.path, u.meta) for u in qs if u.cloud_pct is None or u.cloud_pct < max_cloud]
