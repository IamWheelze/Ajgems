"""Model registry API (FR-101 to FR-104). ML administrators register, compare and propose; a second
person (ML or admin) approves; rollback is one step. Every action is audited."""
from pathlib import Path

from django.conf import settings
from django.http import Http404
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import RolePermission

from . import services
from .models import ModelVersion


def payload(mv):
    return {"id": mv.id, "name": mv.name, "state": mv.state, "input_schema": mv.input_schema,
            "code_commit": mv.code_commit, "dataset": mv.dataset, "licences": mv.licences, "threshold": mv.threshold,
            "metrics": mv.metrics, "card": mv.card, "registered_at": mv.registered_at,
            "registered_by": mv.registered_by.username, "proposed_by": mv.proposed_by.username if mv.proposed_by else None,
            "approved_by": mv.approved_by.username if mv.approved_by else None, "approved_at": mv.approved_at,
            "previous_production": mv.previous_production.name if mv.previous_production else None,
            "reports": [{"id": r.id, "split": r.split, "baseline": r.baseline.name if r.baseline else None,
                         "sites": r.sites, "result": r.result, "created_at": r.created_at} for r in mv.reports.all()]}


class ModelListView(APIView):
    permission_classes = [RolePermission]
    read_roles = ("ml", "admin")
    write_roles = ("ml",)

    def get(self, request):
        return Response([payload(m) for m in ModelVersion.objects.select_related("registered_by", "proposed_by",
                                                                                  "approved_by", "previous_production")])

    def post(self, request):
        """Register a run folder under the data root's runs/ (never an arbitrary server path)."""
        runs_root = (settings.MINEFOOT["DATA_ROOT"] / "runs").resolve()
        run = (runs_root / str(request.data.get("run", ""))).resolve()
        if runs_root not in run.parents:
            return Response({"detail": "give a run folder name inside the runs directory"}, status=400)
        try:
            mv = services.register(request.user, run, request.data.get("name") or None)
        except services.RegistryError as exc:
            return Response({"detail": str(exc)}, status=400)
        return Response(payload(mv), status=201)


class ModelActionView(APIView):
    permission_classes = [RolePermission]
    action_roles = {"compare": ("ml",), "propose": ("ml",), "approve": ("ml", "admin"), "rollback": ("ml", "admin")}
    write_roles = ("ml", "admin")

    def post(self, request, pk, action):
        if action not in self.action_roles or not request.user.has_role(*self.action_roles[action]):
            return Response({"detail": f"{action} is not allowed for your role"}, status=403)
        try:
            mv = ModelVersion.objects.get(pk=pk)
        except ModelVersion.DoesNotExist:
            raise Http404
        try:
            if action == "compare":
                services.compare(request.user, mv, request.data.get("split", "test"))
            else:
                mv = getattr(services, action)(request.user, mv)
        except services.RegistryError as exc:
            return Response({"detail": str(exc)}, status=400)
        mv.refresh_from_db()
        return Response(payload(mv))
