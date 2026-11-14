"""Model registry API (FR-101 to FR-104). ML administrators register, compare and propose; a second
person (ML or admin) approves; rollback is one step. Every action is audited."""
import json
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
            "approved_scope": mv.approved_scope, "approval_review": mv.approval_review,
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
            elif action == "approve":
                mv = services.approve(request.user, mv, request.data.get("scope"), request.data.get("reasons") or {})
            else:
                mv = getattr(services, action)(request.user, mv)
        except services.RegistryError as exc:
            return Response({"detail": str(exc)}, status=400)
        mv.refresh_from_db()
        return Response(payload(mv))


def run_dashboard(run_dir: Path) -> dict:
    """FR-130: what the run dashboard charts, read straight from the run's stored records.

    history.csv (training curves), metrics.json (per-stratum metrics with 95% intervals) and, when
    `minefoot curves` has been run, curves.json (precision-recall and reliability, checked against
    metrics.json when it was written). Nothing here is recomputed or estimated.
    """
    import csv
    out = {"run": run_dir.name, "history": [], "metrics": None, "curves": None, "missing": []}
    hist = run_dir / "history.csv"
    if hist.exists():
        with hist.open() as f:
            out["history"] = [{"epoch": int(r["epoch"]), "train_loss": float(r["train_loss"]),
                               "val_chip_loss": float(r["val_chip_loss"]), "lr": float(r["lr"]),
                               "checkpoint": r.get("checkpoint") == "True"} for r in csv.DictReader(f)]
    else:
        out["missing"].append("history.csv")
    try:
        m = json.loads((run_dir / "metrics.json").read_text())
        u = m["models"]["unet"]
        out["metrics"] = {"best_epoch": m.get("best_epoch"), "epochs": m.get("epochs"), "runtime_s": m.get("runtime_s"),
                          "threshold_score": u["threshold_score"],
                          "strata": {k: u[k]["strata"] for k in ("val", "test")}}
    except (OSError, KeyError, ValueError):
        out["missing"].append("metrics.json")
    try:
        c = json.loads((run_dir / "curves.json").read_text())
        out["curves"] = {"created_at": c["created_at"], **{k: c["models"]["unet"][k] for k in ("val", "test")}}
    except (OSError, KeyError, ValueError):
        out["missing"].append("curves.json (run `minefoot curves`)")
    out["calibration"] = None
    try:
        c = json.loads((run_dir / "calibration.json").read_text())
        keep = ("ece", "ece_active", "brier", "ci95", "pixels")
        out["calibration"] = {
            "created_at": c["created_at"], "applied_method": c["applied_method"], "platt": c["platt"],
            "at_threshold": c["at_threshold"], "note": c.get("note"),
            **{k: {s: {"sites": v["sites"], **{m: {f: v[m].get(f) for f in keep} for m in ("raw", "platt", "isotonic")}}
                   for s, v in c[k].items()} for k in ("val", "test")}}
    except (OSError, KeyError, ValueError):
        pass   # optional: the dashboard says how to produce it
    return services.json_safe(out)


class ModelDashboardView(APIView):
    permission_classes = [RolePermission]
    read_roles = ("ml", "admin")

    def get(self, request, pk):
        try:
            mv = ModelVersion.objects.get(pk=pk)
        except ModelVersion.DoesNotExist:
            raise Http404
        return Response({"id": mv.id, "name": mv.name, "state": mv.state, **run_dashboard(Path(mv.run_path))})


class ErrorSitesView(APIView):
    """FR-129: a registered model's test sites ranked by worst pixel F1, with their error maps."""
    permission_classes = [RolePermission]
    read_roles = ("ml", "admin")

    def get(self, request, pk):
        from .errors import site_rows
        try:
            mv = ModelVersion.objects.get(pk=pk)
        except ModelVersion.DoesNotExist:
            raise Http404
        try:
            rows = site_rows(mv, request.query_params.get("split", "test"))
        except (OSError, KeyError) as exc:
            return Response({"detail": f"error maps unavailable: {exc}"}, status=400)
        return Response({"model": mv.name, "split": "test", "sites": services.json_safe(rows)})


class ErrorImageView(APIView):
    permission_classes = [RolePermission]
    read_roles = ("ml", "admin")

    def get(self, request, pk, site_id):
        from django.http import FileResponse
        from .errors import render
        try:
            mv = ModelVersion.objects.get(pk=pk)
        except ModelVersion.DoesNotExist:
            raise Http404
        classes = request.query_params.get("classes", "hit,false_alarm,missed,not_assessed").split(",")
        try:
            path, bounds = render(mv, site_id, request.query_params.get("layer", "errors"), classes)
        except (ValueError, OSError) as exc:
            return Response({"detail": str(exc)}, status=400)
        if request.query_params.get("bounds") == "1":
            return Response({"bounds": bounds})
        return FileResponse(open(path, "rb"), content_type="image/png")


class ReleaseReviewView(APIView):
    """FR-104: what the approver must weigh: open issues per scope and the evidence."""
    permission_classes = [RolePermission]
    read_roles = ("ml", "admin")

    def get(self, request, pk):
        from .release import SCOPES, evidence, issues
        try:
            mv = ModelVersion.objects.get(pk=pk)
        except ModelVersion.DoesNotExist:
            raise Http404
        iss = issues(mv)
        return Response(services.json_safe({"model": mv.name, "state": mv.state, "scopes": SCOPES, "issues": iss,
                                            "allowed_scopes": [s for s in SCOPES if not any(s in i["blocks"] for i in iss)],
                                            "evidence": evidence(mv), "card": mv.card, "licences": mv.licences}))
