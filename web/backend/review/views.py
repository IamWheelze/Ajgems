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


class FieldTasksView(APIView):
    """FR-095: open field-check tasks for the phone to take offline."""
    permission_classes = [RolePermission]
    read_roles = ("field", "gis", "admin")

    def get(self, request):
        from .field import tasks
        return Response(tasks(request.user))


class FieldVisitSyncView(APIView):
    """POST multipart: meta (JSON), photos (files), blurred (JSON list of counts). Idempotent by meta.client_uuid."""
    permission_classes = [RolePermission]
    write_roles = ("field",)

    def post(self, request):
        import json
        from .field import sync_visit, visit_payload
        try:
            meta = json.loads(request.data.get("meta") or "{}")
            blurred = json.loads(request.data.get("blurred") or "[]")
        except ValueError:
            return Response({"detail": "meta and blurred must be JSON"}, status=400)
        try:
            v, created = sync_visit(request.user, meta, [f.read() for f in request.FILES.getlist("photos")], blurred)
        except Detection.DoesNotExist:
            raise Http404
        except PermissionError as exc:
            return Response({"detail": str(exc)}, status=403)
        except ReviewError as exc:
            return Response({"detail": str(exc)}, status=400)
        return Response(visit_payload(v), status=201 if created else 200)


class FieldVisitListView(APIView):
    permission_classes = [RolePermission]
    read_roles = ("field", "gis", "admin")

    def get(self, request, pk):
        from .field import visit_payload
        from .models import FieldVisit
        qs = (FieldVisit.objects.filter(detection_id=pk, organisation=request.user.organisation)
              .select_related("observer", "review").prefetch_related("photos").order_by("visited_at"))
        return Response([visit_payload(v) for v in qs])


class FieldPhotoView(APIView):
    """Photos open only for field, GIS and admin users of the same organisation; never for customer viewers."""
    permission_classes = [RolePermission]
    read_roles = ("field", "gis", "admin")

    def get(self, request, pk):
        from django.http import FileResponse
        from .models import FieldPhoto
        try:
            p = FieldPhoto.objects.get(pk=pk, organisation=request.user.organisation)
        except FieldPhoto.DoesNotExist:
            raise Http404
        return FileResponse(open(p.path, "rb"), content_type="image/jpeg")


class AnnotationTaskListView(APIView):
    """FR-076: GET tasks; POST {tile} creates a task on a job tile's imagery (GIS or ML)."""
    permission_classes = [RolePermission]
    read_roles = ("gis", "ml", "admin")
    write_roles = ("gis", "ml")

    def get(self, request):
        from .annotation import payload
        from .models import AnnotationTask
        qs = AnnotationTask.objects.filter(organisation=request.user.organisation).select_related("tile__scene", "created_by")
        return Response([payload(t) for t in qs.order_by("-created_at")])

    def post(self, request):
        from projects.models import Tile
        from .annotation import create, payload
        try:
            return Response(payload(create(request.user, request.data.get("tile"))), status=201)
        except Tile.DoesNotExist:
            raise Http404
        except PermissionError as exc:
            return Response({"detail": str(exc)}, status=403)
        except ReviewError as exc:
            return Response({"detail": str(exc)}, status=400)


class AnnotationTaskView(APIView):
    """GET the task with its current labels; POST edit/ {op, class, coordinates?, radius_m?, indices?, index?};
    POST state/ {state}; GET mask/ (zip: mask.tif, labels.geojson, summary.json); GET image/?layer=."""
    permission_classes = [RolePermission]
    read_roles = ("gis", "ml", "admin")
    write_roles = ("gis", "ml")

    def _task(self, request, pk):
        from .models import AnnotationTask
        try:
            return AnnotationTask.objects.select_related("tile__scene", "tile__job", "created_by").get(pk=pk, organisation=request.user.organisation)
        except AnnotationTask.DoesNotExist:
            raise Http404

    def get(self, request, pk, part=None):
        import io
        import json
        import zipfile
        from django.http import FileResponse, HttpResponse
        from .annotation import export_mask, features, payload
        from .models import LABEL_CLASSES
        task = self._task(request, pk)
        if part == "mask":
            tif, gj, summary = export_mask(task)
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                z.writestr("mask.tif", tif); z.writestr("labels.geojson", gj)
                z.writestr("summary.json", json.dumps({**summary, "classes": {i + 1: c for i, c in enumerate(LABEL_CLASSES)},
                                                       "protocol_version": task.protocol_version, "tile": task.tile.grid_id}, indent=1))
            r = HttpResponse(buf.getvalue(), content_type="application/zip")
            r["Content-Disposition"] = f'attachment; filename="annotation_{task.pk}_{task.tile.grid_id}.zip"'
            return r
        if part == "image":
            from types import SimpleNamespace
            from projects.imagery import render_detection_png
            t = task.tile
            try:
                path, bounds = render_detection_png(SimpleNamespace(job=t.job, geometry=t.geometry, scene=t.scene),
                                                    request.query_params.get("layer", "true_colour"), only_tiles=[t])
            except ValueError as exc:
                return Response({"detail": str(exc)}, status=400)
            if request.query_params.get("bounds") == "1":
                return Response({"bounds": bounds})
            return FileResponse(open(path, "rb"), content_type="image/png")
        return Response({**payload(task), "labels": features(task), "classes": LABEL_CLASSES})

    def post(self, request, pk, part=None):
        from .annotation import edit, payload
        task = self._task(request, pk)
        if part == "state":
            st = request.data.get("state")
            if st not in ("open", "done"):
                return Response({"detail": "state must be open or done"}, status=400)
            task.state = st; task.save(update_fields=["state"])
            from audit.models import record
            record(request.user, f"annotation.{st}", task)
            return Response(payload(task))
        if part != "edit":
            raise Http404
        d = request.data
        try:
            return Response(edit(request.user, pk, d.get("op", ""), d.get("class", "mining"), d))
        except ReviewError as exc:
            return Response({"detail": str(exc)}, status=400)


class CorrectionListView(APIView):
    """FR-096: GET requests (customers see their own); POST {detection, kind, description, contact?}."""
    permission_classes = [RolePermission]
    read_roles = ("admin", "gis", "field", "viewer")
    write_roles = ("admin", "gis", "field", "viewer")

    def get(self, request):
        from .corrections import payload
        from .models import CorrectionRequest
        qs = CorrectionRequest.objects.filter(organisation=request.user.organisation).select_related(
            "detection", "requested_by", "owner").prefetch_related("events__actor").order_by("-created_at")
        if not request.user.has_role("admin", "gis"):
            qs = qs.filter(requested_by=request.user)
        if request.query_params.get("state"):
            qs = qs.filter(state=request.query_params["state"])
        return Response([payload(r) for r in qs])

    def post(self, request):
        from projects.views import DetectionViewSet
        from .corrections import create, payload
        try:   # only candidates the requester can see (viewers: released and not restricted without a grant)
            d = DetectionViewSet()._qs(request).get(pk=request.data.get("detection"))
        except (Detection.DoesNotExist, ValueError, TypeError):
            raise Http404
        try:
            r = create(request.user, d, request.data.get("kind", ""), request.data.get("description", ""), request.data.get("contact", "") or "")
        except ReviewError as exc:
            return Response({"detail": str(exc)}, status=400)
        return Response(payload(r), status=201)


class CorrectionActionView(APIView):
    """POST assign/ {owner} (admin); resolve/ {decision, outcome, apply?} (owner or admin); withdraw/ {note?} (requester)."""
    permission_classes = [RolePermission]
    write_roles = ("admin", "gis", "field", "viewer")

    def post(self, request, pk, action):
        from accounts.models import User
        from . import corrections as c
        from .models import CorrectionRequest
        try:
            if action == "assign":
                try:
                    owner = User.objects.get(username=request.data.get("owner"), organisation=request.user.organisation)
                except User.DoesNotExist:
                    return Response({"detail": "no such user in your organisation"}, status=400)
                r = c.assign(request.user, pk, owner)
            elif action == "resolve":
                r = c.resolve(request.user, pk, request.data.get("decision", ""), request.data.get("outcome", ""), request.data.get("apply"))
            elif action == "withdraw":
                r = c.withdraw(request.user, pk, request.data.get("note", "") or "")
            else:
                raise Http404
        except CorrectionRequest.DoesNotExist:
            raise Http404
        except PermissionError as exc:
            return Response({"detail": str(exc)}, status=403)
        except ReviewError as exc:
            return Response({"detail": str(exc)}, status=400)
        return Response(c.payload(CorrectionRequest.objects.get(pk=r.pk)))
