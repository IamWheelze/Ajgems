"""Register, compare, propose, approve and roll back models. Every step is audited."""
import json
from pathlib import Path

from django.db import transaction
from django.utils import timezone

from audit.models import record

from .models import ComparisonReport, ModelVersion

REQUIRED_CONFIG = ("code_commit", "dataset", "input_schema", "split_file_sha256", "sites", "params")


class RegistryError(ValueError):
    pass


def json_safe(x):
    """NaN and infinity are not valid JSON (PostgreSQL refuses them): store them as null."""
    import math
    if isinstance(x, float):
        return x if math.isfinite(x) else None
    if isinstance(x, dict):
        return {k: json_safe(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [json_safe(v) for v in x]
    return x


def _licences(cfg) -> list:
    out = [{"item": "Copernicus Sentinel-2 imagery", "licence": "Sentinel Data Legal Notice (free, full and open)"},
           {"item": cfg["dataset"]["name"], "licence": cfg["dataset"]["licence"]}]
    lineage = cfg.get("pretrained_lineage")
    if lineage is None:   # runs from before 2026-11-13: decide from the note and the parameters
        lineage = ["pretrained encoder weights"] if (cfg.get("encoder_weights_note") or cfg.get("params", {}).get(
            "encoder_weights") not in (None, "none", "None") or cfg.get("init_from")) else []
    if lineage:
        out.append({"item": "pretrained encoder weights", "licence": "NOT VERIFIED (see docs/licence_register.csv)",
                    "lineage": lineage})
    if cfg.get("hard_negatives"):
        out.append({"item": "hard negatives", "licence": str(cfg["hard_negatives"].get("source"))})
    return out


def register(user, run_dir, name=None) -> ModelVersion:
    """FR-101: a run is registered only if every lineage field is present."""
    from minefoot.schema import SCHEMA_VERSION
    run = Path(run_dir)
    missing_files = [f for f in ("config.json", "metrics.json", "normalisation.json", "unet_best.pt") if not (run / f).exists()]
    if missing_files:
        raise RegistryError(f"run is missing {missing_files}")
    cfg = json.loads((run / "config.json").read_text())
    missing = [k for k in REQUIRED_CONFIG if not cfg.get(k)]
    if missing:
        raise RegistryError(f"lineage fields missing from config.json: {missing}")
    if "+uncommitted" in cfg["code_commit"]:
        raise RegistryError("run was made from uncommitted code; its lineage cannot be reproduced")
    m = json.loads((run / "metrics.json").read_text())["models"]["unet"]
    name = name or run.name
    if ModelVersion.objects.filter(name=name).exists():
        raise RegistryError(f"a model named {name} is already registered")
    summary = {k: {kk: m[k]["strata"]["all"][kk] for kk in ("f1", "precision", "recall", "false_alarm_km2_per_1000km2")
                   if kk in m[k]["strata"]["all"]} for k in ("val", "test")}
    mv = ModelVersion.objects.create(
        name=name, run_path=str(run.resolve()), input_schema=cfg["input_schema"], code_commit=cfg["code_commit"],
        dataset=cfg["dataset"], licences=_licences(cfg), normalisation=json.loads((run / "normalisation.json").read_text()),
        threshold=m["threshold_score"], metrics=json_safe(summary), registered_by=user,
        card={"intended_use": "Find candidate footprints of visible land disturbance resembling surface mining, for human review.",
              "not_for": "Mineral type, ownership, legality or current activity (FR-110). Not a calibrated probability.",
              "trained_on": f"{cfg['dataset']['name']} {cfg['dataset']['version']}",
              "validated_domain": "Global ML-ready test sites only; not yet tested on Nigerian sites (G3 pending).",
              "schema_matches_inference": cfg["input_schema"] == SCHEMA_VERSION})
    record(user, "model.register", mv, after={"name": name, "run": str(run), "code_commit": mv.code_commit})
    return mv


def production():
    return ModelVersion.objects.filter(state=ModelVersion.State.PRODUCTION).first()


def compare(user, candidate, split="test") -> ComparisonReport:
    """FR-103: paired site bootstrap against production on the same locked sites."""
    from minefoot.baseline import paired_compare
    base = production()
    rows = lambda mv: json.loads((Path(mv.run_path) / "metrics.json").read_text())["models"]["unet"][split]["per_site"]
    if base is None:
        result = {"note": "no production model yet; candidate metrics only", "candidate": candidate.metrics.get(split)}
        sites = len(rows(candidate))
    else:
        if base.pk == candidate.pk:
            raise RegistryError("a model cannot be compared with itself")
        try:
            result = paired_compare(rows(base), rows(candidate), n_boot=2000)
        except ValueError as exc:
            raise RegistryError(f"not comparable on the same locked sites: {exc}")
        sites = result["sites"]
    rep = ComparisonReport.objects.create(candidate=candidate, baseline=base, split=split, sites=sites, result=json_safe(result),
                                          created_by=user)
    if candidate.state == ModelVersion.State.EXPERIMENTAL:
        candidate.state = ModelVersion.State.UNDER_EVALUATION
        candidate.save(update_fields=["state"])
    record(user, "model.compare", candidate, after={"report": rep.pk, "baseline": base.name if base else None})
    return rep


def propose(user, mv) -> ModelVersion:
    if mv.state != ModelVersion.State.UNDER_EVALUATION:
        raise RegistryError(f"only a model under evaluation can be proposed, this one is {mv.state}")
    if not mv.reports.exists():
        raise RegistryError("compare the model with production first (FR-103)")
    if not mv.card.get("schema_matches_inference"):
        raise RegistryError("the model's input schema does not match inference (FR-102)")
    mv.state, mv.proposed_by, mv.proposed_at = ModelVersion.State.CANDIDATE, user, timezone.now()
    mv.save(update_fields=["state", "proposed_by", "proposed_at"])
    record(user, "model.propose", mv)
    return mv


def approve(user, mv, scope=None, reasons=None) -> ModelVersion:
    """FR-104: a second person approves for a stated scope, after the release review; the model becomes production
    and the old one is kept for rollback."""
    if mv.state != ModelVersion.State.CANDIDATE:
        raise RegistryError(f"only a proposed candidate can be approved, this one is {mv.state}")
    if user.pk in (mv.proposed_by_id, mv.registered_by_id):
        raise RegistryError("a second person must approve: you registered or proposed this model")
    from .release import check
    try:
        review = check(mv, scope, reasons)
    except ValueError as exc:
        raise RegistryError(str(exc))
    with transaction.atomic():
        old = production()
        if old:
            old.state = ModelVersion.State.RETIRED
            old.save(update_fields=["state"])
        mv.state, mv.approved_by, mv.approved_at, mv.previous_production = (
            ModelVersion.State.PRODUCTION, user, timezone.now(), old)
        mv.approved_scope, mv.approval_review = scope, review
        mv.save(update_fields=["state", "approved_by", "approved_at", "previous_production", "approved_scope", "approval_review"])
        record(user, "model.approve", mv, after={"replaced": old.name if old else None, "scope": scope,
                                                  "reasons": {i["id"]: i["reason"] for i in review["issues"]}})
    return mv


def rollback(user, mv) -> ModelVersion:
    """One step back to the previous production model, logged."""
    if mv.state != ModelVersion.State.PRODUCTION or mv.previous_production is None:
        raise RegistryError("only a production model with a predecessor can be rolled back")
    with transaction.atomic():
        prev = mv.previous_production
        mv.state = ModelVersion.State.ROLLED_BACK
        mv.save(update_fields=["state"])
        prev.state = ModelVersion.State.PRODUCTION
        prev.save(update_fields=["state"])
        record(user, "model.rollback", mv, after={"restored": prev.name})
    return prev
