"""Review endpoints (FR-091, FR-092): submit a decision, list the options."""
from django.http import Http404
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import RolePermission
from projects.models import Detection
from projects.serializers import detection_feature

from .models import ACTIONS, REASONS, MissedSite
from .services import ReviewError, submit_review


class ReviewCreateView(APIView):
    """POST {action, reason_code, note?, geometry?}. The detection's history is append-only."""
    permission_classes = [RolePermission]
    write_roles = ("gis", "field")

    def post(self, request, pk):
        try:
            submit_review(request.user, pk, request.data.get("action", ""), request.data.get("reason_code", ""),
                          request.data.get("note", "") or "", request.data.get("geometry"))
        except Detection.DoesNotExist:
            raise Http404
        except PermissionError as exc:
            return Response({"detail": str(exc)}, status=403)
        except ReviewError as exc:
            return Response({"detail": str(exc)}, status=400)
        d = Detection.objects.prefetch_related("reviews").select_related("scene").get(pk=pk)
        return Response(detection_feature(d, include_history=True), status=201)


class ReviewOptionsView(APIView):
    permission_classes = [RolePermission]
    read_roles = ("admin", "gis", "field", "viewer")

    def get(self, request):
        return Response({a: {"status_after": s, "roles": sorted(r), "allowed_from": sorted(f) if f else None,
                             "reasons": REASONS[a]} for a, (s, r, f) in ACTIONS.items()})


class MissedSiteView(APIView):
    """FR-093: GET lists a project's missed sites; POST {geometry, reason_code, note?} adds one."""
    permission_classes = [RolePermission]
    read_roles = ("admin", "gis", "field")
    write_roles = ("gis",)

    def get(self, request, project_pk):
        from projects.exports import selected_jobs
        from projects.models import Project
        from .models import MissedSite
        from .services import missed_feature
        try:
            p = Project.objects.get(pk=project_pk, organisation=request.user.organisation)
        except Project.DoesNotExist:
            raise Http404
        qs = MissedSite.objects.filter(project=p).select_related("added_by")
        if request.query_params.get("all") != "1":   # same default as detections: the latest finished job per area
            qs = qs.filter(job__in=selected_jobs(p))
        from .models import MISSED_REASONS
        return Response({"type": "FeatureCollection", "features": [missed_feature(m) for m in qs], "reasons": MISSED_REASONS})

    def post(self, request, project_pk):
        from projects.models import Project
        from .services import add_missed_site, missed_feature
        try:
            ms = add_missed_site(request.user, project_pk, request.data.get("geometry"),
                                 request.data.get("reason_code", ""), request.data.get("note", "") or "")
        except Project.DoesNotExist:
            raise Http404
        except PermissionError as exc:
            return Response({"detail": str(exc)}, status=403)
        except ReviewError as exc:
            return Response({"detail": str(exc)}, status=400)
        return Response(missed_feature(ms), status=201)


class PoolView(APIView):
    """FR-075 annotation pool. GET ?state=nominated|accepted|returned; POST {detection | missed_site, note?}."""
    permission_classes = [RolePermission]
    read_roles = ("admin", "gis", "ml")
    write_roles = ("gis",)

    def get(self, request):
        from .models import Nomination
        from .pool import feature, state
        qs = Nomination.objects.filter(organisation=request.user.organisation).select_related("nominated_by", "missed_site")
        want = request.query_params.get("state")
        feats = [feature(n, request.user) for n in qs]
        if want:
            feats = [f for f in feats if f["properties"]["state"] == want]
        from .models import QC_DECISIONS
        return Response({"type": "FeatureCollection", "features": feats, "decisions": QC_DECISIONS})

    def post(self, request):
        from projects.models import Detection
        from .pool import feature, nominate
        try:
            n = nominate(request.user, request.data.get("detection"), request.data.get("missed_site"), request.data.get("note", "") or "")
        except (Detection.DoesNotExist, MissedSite.DoesNotExist):
            raise Http404
        except PermissionError as exc:
            return Response({"detail": str(exc)}, status=403)
        except ReviewError as exc:
            return Response({"detail": str(exc)}, status=400)
        return Response(feature(n, request.user), status=201)


class PoolDecideView(APIView):
    permission_classes = [RolePermission]
    write_roles = ("gis", "ml")

    def post(self, request, pk):
        from .models import Nomination
        from .pool import decide, feature
        try:
            decide(request.user, pk, request.data.get("decision", ""), request.data.get("reason_code", ""), request.data.get("note", "") or "")
        except Nomination.DoesNotExist:
            raise Http404
        except PermissionError as exc:
            return Response({"detail": str(exc)}, status=403)
        except ReviewError as exc:
            return Response({"detail": str(exc)}, status=400)
        return Response(feature(Nomination.objects.get(pk=pk), request.user))


class PoolExportView(APIView):
    """Accepted examples only: a zip of labels.geojson and manifest.json (ML role)."""
    permission_classes = [RolePermission]
    read_roles = ("ml",)

    def get(self, request):
        import io
        import json
        import zipfile
        from django.http import HttpResponse
        from .pool import export_accepted
        body, manifest = export_accepted(request.user)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("labels.geojson", body)
            z.writestr("manifest.json", json.dumps(manifest, indent=2))
        r = HttpResponse(buf.getvalue(), content_type="application/zip")
        r["Content-Disposition"] = 'attachment; filename="annotation_pool_accepted.zip"'
        return r


class PoolImageView(APIView):
    """Imagery under a nomination: its detection's mosaic, or the missed site's job tiles."""
    permission_classes = [RolePermission]
    read_roles = ("admin", "gis", "ml")

    def get(self, request, pk):
        from types import SimpleNamespace
        from django.http import FileResponse
        from projects.imagery import render_detection_png
        from .models import Nomination
        try:
            n = Nomination.objects.select_related("detection__job", "detection__scene", "missed_site__job").get(
                pk=pk, organisation=request.user.organisation)
        except Nomination.DoesNotExist:
            raise Http404
        src = n.detection
        if src is None:
            m = n.missed_site
            tile = m.job.tiles.filter(geometry__intersects=m.geometry.point_on_surface).select_related("scene").first()
            src = SimpleNamespace(job=m.job, geometry=m.geometry, scene=tile.scene if tile else None)
        layer = request.query_params.get("layer", "true_colour")
        try:
            path, bounds = render_detection_png(src, layer)
        except (ValueError, AttributeError) as exc:
            return Response({"detail": str(exc) or "no imagery for this example"}, status=400)
        if request.query_params.get("bounds") == "1":
            return Response({"bounds": bounds})
        return FileResponse(open(path, "rb"), content_type="image/png")
