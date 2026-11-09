"""Project, area, detection and scene endpoints. Every view checks roles (FR-002) and is
scoped to the user's organisation (FR-003)."""
from django.conf import settings
from django.db import IntegrityError, transaction
from django.http import FileResponse, Http404
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.views import APIView
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response

from accounts.permissions import OrgScopedMixin, RolePermission
from audit.models import record

from . import aoi
from .models import AreaOfInterest, Detection, Project, Scene
from .serializers import AreaSerializer, ProjectSerializer, detection_feature

ALL = ("admin", "ml", "gis", "field", "viewer")


class ProjectViewSet(OrgScopedMixin, viewsets.ModelViewSet):
    queryset = Project.objects.all()
    serializer_class = ProjectSerializer
    permission_classes = [RolePermission]
    read_roles = ALL
    write_roles = ("gis",)
    action_roles = {"archive": ("gis", "admin")}
    http_method_names = ["get", "post", "patch", "head", "options"]   # projects are archived, never deleted

    def perform_create(self, serializer):
        u = self.request.user
        try:
            p = serializer.save(organisation=u.organisation, created_by=u, acceptable_use_accepted_by=u,
                                acceptable_use_accepted_at=timezone.now())
        except IntegrityError:
            from rest_framework.exceptions import ValidationError
            raise ValidationError({"name": "a project with this name already exists"})
        record(u, "project.create", p, after={"name": p.name, "purpose": p.purpose})

    def perform_update(self, serializer):
        p = serializer.instance
        if p.status == Project.Status.ARCHIVED:
            from rest_framework.exceptions import ValidationError
            raise ValidationError("archived projects are read-only")
        before = {"name": p.name, "purpose": p.purpose}
        p = serializer.save()
        record(self.request.user, "project.update", p, before=before, after={"name": p.name, "purpose": p.purpose})

    @action(detail=True, methods=["post"])
    def archive(self, request, pk=None):
        p = self.get_object()
        if p.status != Project.Status.ARCHIVED:
            p.status = Project.Status.ARCHIVED
            p.save(update_fields=["status"])
            record(request.user, "project.archive", p, after={"status": p.status})
        return Response(ProjectSerializer(p).data)


class AreaViewSet(viewsets.ViewSet):
    permission_classes = [RolePermission]
    read_roles = ("admin", "gis", "field", "viewer")
    write_roles = ("gis",)
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def _project(self, request, project_pk):
        try:
            return Project.objects.get(pk=project_pk, organisation=request.user.organisation)
        except Project.DoesNotExist:
            raise Http404

    def list(self, request, project_pk=None):
        p = self._project(request, project_pk)
        return Response(AreaSerializer(p.areas.all(), many=True).data)

    def create(self, request, project_pk=None):
        p = self._project(request, project_pk)
        if p.status == Project.Status.ARCHIVED:
            return Response({"detail": "archived projects are read-only"}, status=400)
        max_km2 = request.user.organisation.max_aoi_km2 or settings.MINEFOOT["AOI_MAX_KM2"]
        try:
            if "file" in request.FILES:
                geom, km2, crs = aoi.from_upload(request.FILES["file"], max_km2)
                source = request.FILES["file"].name.rsplit(".", 1)[-1].lower()
            elif "geometry" in request.data:
                geom, km2, crs = aoi.from_geojson(request.data["geometry"], max_km2)
                source = request.data.get("source", "drawn")
            else:
                return Response({"detail": "send 'geometry' (GeoJSON) or a 'file'"}, status=400)
        except aoi.AoiError as exc:
            return Response({"detail": str(exc)}, status=400)
        a = AreaOfInterest.objects.create(organisation=p.organisation, project=p,
                                          name=request.data.get("name") or f"Area {p.areas.count() + 1}",
                                          geometry=aoi.as_multipolygon(geom), area_km2=km2, source=source[:40],
                                          source_crs=crs[:40], created_by=request.user)
        record(request.user, "area.create", a, after={"project": p.pk, "area_km2": round(km2, 3)})
        return Response(AreaSerializer(a).data, status=status.HTTP_201_CREATED)


class DetectionViewSet(viewsets.ViewSet):
    """Detections as GeoJSON. Customer viewers see released detections only (FRD 5)."""
    permission_classes = [RolePermission]
    read_roles = ("admin", "gis", "field", "viewer")
    write_roles = ()

    def _qs(self, request):
        qs = Detection.objects.filter(organisation=request.user.organisation).select_related("scene")
        if request.user.has_role("viewer") and not request.user.has_role("admin", "gis", "field"):
            qs = qs.filter(status=Detection.Status.RELEASED)
        return qs

    def list(self, request, project_pk=None):
        qs = self._qs(request).filter(project_id=project_pk)
        if not Project.objects.filter(pk=project_pk, organisation=request.user.organisation).exists():
            raise Http404
        for key in ("status", "score_band", "mask_status"):
            if request.query_params.get(key):
                qs = qs.filter(**{f"{key}__in": request.query_params[key].split(",")})
        return Response({"type": "FeatureCollection", "features": [detection_feature(d) for d in qs[:5000]]})

    def retrieve(self, request, pk=None, project_pk=None):
        try:
            d = self._qs(request).prefetch_related("reviews").get(pk=pk)
        except Detection.DoesNotExist:
            raise Http404
        return Response(detection_feature(d, include_history=True))


class SceneImageView(APIView):
    """True-colour (or another display layer) PNG of a scene warped to WGS 84, plus its corner coordinates.

    Only scenes used by the organisation's detections are served (FR-003)."""
    permission_classes = [RolePermission]
    read_roles = ("admin", "gis", "field", "viewer")

    def get(self, request, pk):
        if not Detection.objects.filter(scene_id=pk, organisation=request.user.organisation).exists():
            raise Http404
        scene = Scene.objects.get(pk=pk)
        layer = request.query_params.get("layer", "true_colour")
        from .imagery import render_scene_png
        try:
            path, bounds = render_scene_png(scene, layer)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=400)
        if request.query_params.get("bounds") == "1":
            return Response({"bounds": bounds, "date": scene.acquired_at.isoformat(), "scene_id": scene.source_id,
                             "licence_note": scene.licence_note, "layer": layer})
        return FileResponse(open(path, "rb"), content_type="image/png")


def job_payload(job):
    from collections import Counter
    tiles = list(job.tiles.all())
    return {"id": job.id, "project": job.project_id, "area": job.area_id, "state": job.state,
            "model_version": job.model_version, "message": job.message, "failure_reason": job.failure_reason,
            "estimate": job.estimate, "created_at": job.created_at, "started_at": job.started_at,
            "finished_at": job.finished_at,
            "parameters": {k: v for k, v in job.parameters.items() if k not in ("items",)},
            "tiles": {"total": len(tiles), "by_state": Counter(t.state for t in tiles),
                      "by_outcome": Counter(t.outcome for t in tiles)},
            "candidates": job.detections.count()}


class JobViewSet(viewsets.ViewSet):
    """Detection jobs (FR-080 to FR-085): estimate, start, cancel, resume, status and tile map."""
    permission_classes = [RolePermission]
    read_roles = ("admin", "gis")
    write_roles = ("gis",)

    def _job(self, request, pk):
        from .models import Job
        try:
            return Job.objects.select_related("project").get(pk=pk, organisation=request.user.organisation)
        except Job.DoesNotExist:
            raise Http404

    def list(self, request, project_pk=None):
        from .models import Job
        if not Project.objects.filter(pk=project_pk, organisation=request.user.organisation).exists():
            raise Http404
        return Response([job_payload(j) for j in Job.objects.filter(project_id=project_pk).order_by("-created_at")[:50]])

    def create(self, request, project_pk=None):
        from .jobs import JobError, estimate
        try:
            p = Project.objects.get(pk=project_pk, organisation=request.user.organisation)
        except Project.DoesNotExist:
            raise Http404
        d = request.data
        try:
            job = estimate(request.user, p, d.get("area"), d.get("start"), d.get("end"), float(d.get("max_cloud", 20)))
        except JobError as exc:
            return Response({"detail": str(exc)}, status=400)
        except RuntimeError as exc:   # all catalogues failed
            return Response({"detail": f"imagery catalogue unavailable: {exc}"[:400]}, status=503)
        return Response(job_payload(job), status=201)

    def retrieve(self, request, pk=None):
        return Response(job_payload(self._job(request, pk)))

    def tiles(self, request, pk=None):
        import json
        job = self._job(request, pk)
        return Response({"type": "FeatureCollection", "features": [
            {"type": "Feature", "id": t.id, "geometry": json.loads(t.geometry.geojson),
             "properties": {"grid_id": t.grid_id, "state": t.state, "outcome": t.outcome, "usable_pct": t.usable_pct,
                            "quality_decision": t.quality_decision, "candidates": t.candidates,
                            "failure_reason": t.failure_reason,
                            "scene": t.scene.source_id if t.scene_id else None,
                            "date": t.scene.acquired_at.date().isoformat() if t.scene_id else None}}
            for t in job.tiles.select_related("scene").order_by("grid_id")]})

    def _act(self, request, pk, fn):
        from .jobs import JobError
        job = self._job(request, pk)
        try:
            job = fn(request.user, job)
        except JobError as exc:
            return Response({"detail": str(exc)}, status=400)
        return Response(job_payload(job))

    def start(self, request, pk=None):
        from .jobs import start
        return self._act(request, pk, start)

    def cancel(self, request, pk=None):
        from .jobs import cancel
        return self._act(request, pk, cancel)

    def resume(self, request, pk=None):
        from .jobs import resume
        return self._act(request, pk, resume)


class ModelCardView(APIView):
    permission_classes = [RolePermission]
    read_roles = ("admin", "gis", "field", "viewer")

    def get(self, request):
        from .pipeline import model_card
        return Response(model_card())


class ExportView(APIView):
    """POST {format, statuses?, job?} -> zip package (FR-106 to FR-109). Viewers get released candidates only.

    Without `job`, the latest finished job for each area is exported, so repeated runs are not duplicated."""
    permission_classes = [RolePermission]
    write_roles = ("gis", "admin", "viewer")

    def post(self, request, project_pk):
        from django.http import HttpResponse
        from .exports import FORMATS, build_package
        try:
            p = Project.objects.get(pk=project_pk, organisation=request.user.organisation)
        except Project.DoesNotExist:
            raise Http404
        fmt = request.data.get("format", "geojson")
        if fmt not in FORMATS:
            return Response({"detail": f"format must be one of {list(FORMATS)}"}, status=400)
        statuses = request.data.get("statuses") or [s for s in Detection.Status.values if s != "rejected"]
        viewer_only = request.user.has_role("viewer") and not request.user.has_role("admin", "gis")
        if viewer_only:
            statuses = ["released"]
        bad = [s for s in statuses if s not in Detection.Status.values]
        if bad:
            return Response({"detail": f"unknown statuses {bad}"}, status=400)
        from .exports import selected_jobs
        job_id = request.data.get("job")
        jobs = selected_jobs(p, int(job_id) if job_id else None)
        if job_id and not jobs:
            raise Http404
        dets = (Detection.objects.filter(project=p, job__in=jobs, status__in=statuses).select_related("scene")
                .prefetch_related("reviews").order_by("id"))
        data, manifest = build_package(p, dets, request.user, fmt, statuses, jobs)
        record(request.user, "export.create", p, after={"format": fmt, "statuses": statuses, "candidates": manifest["candidates"],
                                                       "files": {k: v["sha256"] for k, v in manifest["files"].items()}})
        resp = HttpResponse(data, content_type="application/zip")
        resp["Content-Disposition"] = f'attachment; filename="project_{p.pk}_{fmt}_export.zip"'
        return resp
