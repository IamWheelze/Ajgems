"""Apply the decision rule declared in docs/g2_results.md (written before g2_s2_hn's results existed) mechanically.

The rule is "g2_s2_hn replaces g2_nw_hn as the commercial candidate" if all three hold, on validation only:
  1. paired F1 (g2_s2_hn minus g2_nw_hn): 95% interval lower bound above -0.02 and point estimate above 0;
  2. false-alarm area not clearly worse: the paired interval for false_alarm_km2_per_1000km2 does not lie wholly above 0;
  3. the licence register still records CC BY 4.0 for the SSL4EO-S12 weights.
Test results are printed for the record and never used here.

Usage: python scripts/g2_s2_decision.py [EVIDENCE_DIR]   (default docs/evidence/g2_s2_hn)
"""
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def decide(ev: Path) -> dict:
    v = json.loads((ev / "paired_vs_g2_nw_hn_val.json").read_text())["metrics"]
    f1, fa = v["f1"], v["false_alarm_km2_per_1000km2"]
    rows = list(csv.DictReader((ROOT / "docs/licence_register.csv").open()))
    lic = [r for r in rows if r["item"].startswith("SSL4EO-S12")]
    checks = {
        "f1_interval_above_-0.02_and_point_above_0": f1["ci95"][0] > -0.02 and f1["b_minus_a"] > 0,
        "false_alarm_area_not_clearly_worse": not fa["ci95"][0] > 0,
        "licence_register_cc_by_4.0": bool(lic) and lic[0]["licence_seen"].startswith("CC BY 4.0"),
    }
    out = {"rule": "docs/g2_results.md, decision rule fixed before results", "checks": checks,
           "val_f1": f1, "val_false_alarm_km2_per_1000km2": fa,
           "commercial_candidate": "g2_s2_hn" if all(checks.values()) else "g2_nw_hn"}
    t = ev / "paired_vs_g2_nw_hn_test.json"
    if t.exists():
        out["test_for_record_only"] = {k: json.loads(t.read_text())["metrics"][k] for k in ("f1", "false_alarm_km2_per_1000km2")}
    return out


if __name__ == "__main__":
    ev = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "docs/evidence/g2_s2_hn"
    res = decide(ev)
    (ev / "decision.json").write_text(json.dumps(res, indent=1))
    print(json.dumps({k: res[k] for k in ("checks", "commercial_candidate")}, indent=1))
