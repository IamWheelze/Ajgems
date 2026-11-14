"""Release review for model approval (FR-104).

Before the second person approves, the system lists every open issue with the model: licences in its lineage
that are not cleared, an untested Nigerian domain (G3), uncalibrated scores, and a missing comparison. Approval is
for one scope: internal research, evaluation or commercial. Each open issue needs the approver's written reason to
proceed at that scope, and some issues block a scope outright. Jobs then refuse projects whose intended use is
wider than the model's approved scope.
"""
import csv
import json
from pathlib import Path

from django.conf import settings

SCOPES = ["internal_research", "evaluation", "commercial"]
MIN_REASON = 20
# Provisional (no FRD figure yet): scores count as calibrated on the measured data only if, on test sites, the applied
# map's ECE over pixels predicted >= 0.1 is at most this, in the pooled set and in every stratum with 10 or more sites.
CALIBRATED_MAX_ECE = 0.05


def _register():
    p = Path(settings.REPO_ROOT) / "docs" / "licence_register.csv"
    return list(csv.DictReader(p.open())) if p.exists() else []


def _licence_status(item, licence):
    """cleared, review (allowed but needs legal advice) or unverified, from the licence register text."""
    text = f"{item} {licence}".lower()
    if "not verified" in text:
        return "unverified"
    if "by-sa" in text or "share-alike" in text:
        return "review"
    if "model-mined" in text or "sentinel" in text or "copernicus" in text:
        return "cleared"
    for r in _register():
        if r["item"].lower().split(" (")[0] in text:
            st = r["commercial_status"].lower()
            return "review" if "review" in st else "unverified" if ("unknown" in st or "not verified" in r["licence_seen"].lower()) else "cleared"
    return "unverified"


def issues(mv):
    """Open issues, each {id, title, detail, blocks: [scopes it blocks outright], needs_reason: [scopes needing a reason]}."""
    out = []
    for lic in mv.licences:
        st = _licence_status(lic["item"], lic["licence"])
        if st == "unverified":
            out.append({"id": f"licence:{lic['item']}", "title": f"Licence not verified: {lic['item']}",
                        "detail": f"{lic['licence']}. No permission for commercial use has been found (docs/licence_register.csv). "
                                  "Retrain without it, or get the terms checked.",
                        "blocks": ["commercial"], "needs_reason": ["internal_research", "evaluation"]})
        elif st == "review":
            out.append({"id": f"licence:{lic['item']}", "title": f"Share-alike licence needs legal advice: {lic['item']}",
                        "detail": f"{lic['licence']}. Whether trained weights or outputs must be shared alike is open "
                                  "(docs/legal_review_brief.md).",
                        "blocks": ["commercial"], "needs_reason": ["internal_research", "evaluation"]})
    nigeria = Path(settings.MINEFOOT["NIGERIA_G3_RESULT"])
    if not nigeria.exists():
        out.append({"id": "domain:nigeria", "title": "Not tested on Nigerian sites (gate G3 not run)",
                    "detail": "Performance in Nigeria is unknown; global test F1 does not transfer automatically. "
                              "Lock the Nigerian labels and run G3.",
                    "blocks": ["commercial"], "needs_reason": ["internal_research", "evaluation"]})
    cal_issue = _calibration_issue(Path(mv.run_path))
    if cal_issue:
        out.append(cal_issue)
    if not any((r.result or {}).get("metrics") for r in mv.reports.all()):
        out.append({"id": "comparison", "title": "No paired comparison with a production model",
                    "detail": "There is no production model yet, so the registry comparison has no baseline. "
                              "Check the paired comparisons with earlier runs shown above.",
                    "blocks": [], "needs_reason": SCOPES})
    return out


def _calibration_issue(run: Path):
    """From calibration.json (`minefoot calibrate`) when present, else from the reliability table in curves.json."""
    try:
        c = json.loads((run / "calibration.json").read_text())
        method = c["applied_method"]; test = c["test"]
        a = test["all"]
        bad = {k: v[method]["ece_active"] for k, v in test.items()
               if (k == "all" or v["sites"] >= 10) and v[method]["ece_active"] is not None and v[method]["ece_active"] > CALIBRATED_MAX_ECE}
        if not bad:
            return {"id": "calibration", "title": "Calibration measured on global test sites only",
                    "detail": f"After {method} scaling fitted on validation, ECE on test pixels predicted >= 0.1 is "
                              f"{a[method]['ece_active']:.3f}. This holds only for the data measured; recheck per region and season. "
                              "Nothing applies the map, so outputs still say \"model score\".",
                    "blocks": [], "needs_reason": SCOPES}
        worst = max(bad, key=bad.get)
        ci = a[method].get("ci95") or {}
        return {"id": "calibration", "title": "Scores are not calibrated",
                "detail": f"On test sites, ECE over pixels predicted >= 0.1 is {a['raw']['ece_active']:.3f} raw and "
                          f"{a[method]['ece_active']:.3f} after {method} scaling fitted on validation"
                          + (f" (95% interval {ci['ece_active'][0]:.3f}-{ci['ece_active'][1]:.3f})" if ci.get("ece_active") else "")
                          + f"; worst stratum {worst} at {bad[worst]:.3f} (provisional limit {CALIBRATED_MAX_ECE}). "
                          "Outputs must say \"model score\", never probability.",
                "blocks": [], "needs_reason": SCOPES}
    except (OSError, KeyError, ValueError, TypeError):
        pass
    try:
        c = json.loads((run / "curves.json").read_text())["models"]["unet"]["test"]["reliability"]
        top = next(b for b in reversed(c) if b["pixels"])
        if top["labelled_fraction"] is not None and top["mean_score"] - top["labelled_fraction"] > 0.15:
            return {"id": "calibration", "title": "Scores are not calibrated",
                    "detail": f"On test sites, pixels scored {top['score_from']:.1f}-{top['score_to']:.1f} are only "
                              f"{top['labelled_fraction']:.0%} labelled mining. Outputs must say \"model score\", never probability. "
                              "Run `minefoot calibrate` for a full calibration study.",
                    "blocks": [], "needs_reason": SCOPES}
        return None
    except (OSError, KeyError, ValueError, StopIteration):
        return {"id": "calibration", "title": "Calibration not measured", "detail": "Run `minefoot calibrate` on this run.",
                "blocks": [], "needs_reason": SCOPES}


def evidence(mv):
    """Numbers the approver should see: test metrics with intervals, and paired comparisons stored with the run."""
    run = Path(mv.run_path)
    out = {"test": (mv.metrics or {}).get("test"), "val": (mv.metrics or {}).get("val"), "paired": {}}
    try:   # intervals from the run's own record (the registry summary keeps point values only)
        allm = json.loads((run / "metrics.json").read_text())["models"]["unet"]["test"]["strata"]["all"]
        out["test"] = {**(out["test"] or {}), "ci95": allm.get("ci95", {}), "sites": allm.get("sites")}
    except (OSError, KeyError, ValueError):
        pass
    ev = Path(settings.REPO_ROOT) / "docs" / "evidence" / run.name
    for f in sorted(ev.glob("paired_vs_*_test.json")):
        try:
            m = json.loads(f.read_text())["metrics"]
            out["paired"][f.stem.replace("paired_vs_", "").replace("_test", "")] = {
                k: {"b_minus_a": v["b_minus_a"], "ci95": v["ci95"], "verdict": v["verdict"]}
                for k, v in m.items() if k in ("f1", "precision", "recall", "false_alarm_km2_per_1000km2")}
        except (OSError, KeyError, ValueError):
            pass
    return out


def check(mv, scope, reasons):
    """Raise ValueError naming what stops approval at this scope; return the review record to store."""
    if scope not in SCOPES:
        raise ValueError(f"choose the scope you approve for: {SCOPES}")
    blocking = [i["title"] for i in issues(mv) if scope in i["blocks"]]
    if blocking:
        raise ValueError(f"cannot approve for {scope.replace('_', ' ')} use while: " + "; ".join(blocking))
    missing = [i["title"] for i in issues(mv) if scope in i["needs_reason"]
               and len((reasons or {}).get(i["id"], "").strip()) < MIN_REASON]
    if missing:
        raise ValueError(f"write a reason (at least {MIN_REASON} characters) for each open issue: " + "; ".join(missing))
    return {"scope": scope, "issues": [{**i, "reason": (reasons or {}).get(i["id"], "")} for i in issues(mv)]}


def covers(model_scope, use):
    """Is a model approved for `model_scope` usable by a project with this intended use?"""
    return model_scope in SCOPES and SCOPES.index(model_scope) >= SCOPES.index(use)
