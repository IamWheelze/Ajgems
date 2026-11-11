"""FR-105 monitoring: input and output drift and review-rejection rates per area and season, with alerts.

Groups are (area of interest, season of the job's imagery). Seasons are meteorological quarters (Dec-Feb, Mar-May,
Jun-Aug, Sep-Nov); in Nigeria Dec-Feb is the dry Harmattan season and Jun-Aug the wet season. Thresholds are
provisional and listed with every report, so an alert always says which rule fired.
"""
from collections import defaultdict
from pathlib import Path

INPUT_SHIFT_MAX = 1.0          # |tile band mean - training mean| in training standard deviations
OUTPUT_RATIO = (1 / 3, 3.0)     # candidate area per usable area, as a ratio to the validation rate
OUTPUT_MIN_KM2 = 50             # usable area needed before the output rate is judged
REJECT_MAX, REJECT_MIN_N = 0.5, 10
SEASONS = {12: "Dec-Feb", 1: "Dec-Feb", 2: "Dec-Feb", 3: "Mar-May", 4: "Mar-May", 5: "Mar-May",
           6: "Jun-Aug", 7: "Jun-Aug", 8: "Jun-Aug", 9: "Sep-Nov", 10: "Sep-Nov", 11: "Sep-Nov"}
REVIEWED = {"confirmed", "edited", "rejected", "field_confirmed", "field_rejected", "released"}
REJECTED = {"rejected", "field_rejected"}


def _run_dir(job):
    from .pipeline import active_model
    return Path(job.parameters.get("model_run") or active_model()[0])


def report(org):
    from minefoot.drift import reference_rate, shift
    from .models import Job
    groups = defaultdict(lambda: {"jobs": [], "tiles": 0, "usable_km2": 0.0, "candidate_km2": 0.0, "shifts": [],
                                  "reviewed": 0, "rejected": 0, "runs": set()})
    jobs = (Job.objects.filter(organisation=org, state__in=("succeeded", "succeeded_with_warnings"), kind="detection")
            .exclude(area=None).select_related("area").prefetch_related("tiles__scene", "detections"))
    refs = {}
    for j in jobs:
        tiles = [t for t in j.tiles.all() if t.scene_id and t.usable_pct is not None]
        if not tiles:
            continue
        month = max(tiles, key=lambda t: t.usable_pct).scene.acquired_at.month
        g = groups[(j.area.name, SEASONS[month])]
        run = _run_dir(j)
        g["jobs"].append(j.pk); g["runs"].add(run.name)
        if run.name not in refs:
            try:
                refs[run.name] = reference_rate(run)
            except (OSError, KeyError, ValueError):
                refs[run.name] = None
        for t in tiles:
            g["tiles"] += 1
            g["usable_km2"] += (t.geometry.transform(6933, clone=True).area / 1e6) * t.usable_pct / 100
            if t.input_stats.get("means"):
                try:
                    g["shifts"].append(shift(t.input_stats["means"], run))
                except (OSError, KeyError, ValueError):
                    pass
        for d in j.detections.all():
            g["candidate_km2"] += d.area_ha / 100
            if d.status in REVIEWED:
                g["reviewed"] += 1
                g["rejected"] += d.status in REJECTED
    out, alerts = [], []
    for (area, season), g in sorted(groups.items()):
        bands = {}
        for s in g["shifts"]:
            for b, v in s.items():
                if v is not None:
                    bands.setdefault(b, []).append(v)
        mean_shift = {b: round(sum(v) / len(v), 2) for b, v in bands.items()}
        ref = next((refs[r] for r in g["runs"] if refs.get(r)), None)
        rate = 1000 * g["candidate_km2"] / g["usable_km2"] if g["usable_km2"] else None
        ratio = rate / ref if rate is not None and ref else None
        rej = g["rejected"] / g["reviewed"] if g["reviewed"] else None
        row = {"area": area, "season": season, "jobs": g["jobs"], "tiles": g["tiles"], "model_runs": sorted(g["runs"]),
               "usable_km2": round(g["usable_km2"], 1), "input_shift": mean_shift,
               "candidate_km2_per_1000km2": None if rate is None else round(rate, 1),
               "reference_km2_per_1000km2": None if ref is None else round(ref, 1),
               "output_ratio": None if ratio is None else round(ratio, 2),
               "reviewed": g["reviewed"], "rejection_rate": None if rej is None else round(rej, 2)}
        worst = max(mean_shift.items(), key=lambda kv: abs(kv[1]), default=None)
        if worst and abs(worst[1]) > INPUT_SHIFT_MAX:
            alerts.append({**_where(row), "kind": "input_drift", "message": f"{worst[0]} reflectance is {worst[1]:+.1f} training SDs "
                           f"from the training mean (limit {INPUT_SHIFT_MAX}). Inputs differ from what the model learned on."})
        if ratio is not None and g["usable_km2"] >= OUTPUT_MIN_KM2 and not OUTPUT_RATIO[0] <= ratio <= OUTPUT_RATIO[1]:
            alerts.append({**_where(row), "kind": "output_drift", "message": f"{rate:.1f} km² of candidates per 1,000 km² is {ratio:.1f}× "
                           f"the model's validation rate ({ref:.1f}). Outputs differ from what was checked."})
        if rej is not None and g["reviewed"] >= REJECT_MIN_N and rej >= REJECT_MAX:
            alerts.append({**_where(row), "kind": "review_rejections", "message": f"reviewers rejected {g['rejected']} of "
                           f"{g['reviewed']} reviewed candidates ({rej:.0%}, limit {REJECT_MAX:.0%})."})
        out.append(row)
    return {"groups": out, "alerts": alerts,
            "thresholds": {"input_shift_max_sd": INPUT_SHIFT_MAX, "output_ratio_range": OUTPUT_RATIO, "output_min_usable_km2": OUTPUT_MIN_KM2,
                           "rejection_rate_max": REJECT_MAX, "rejection_min_reviewed": REJECT_MIN_N, "provisional": True}}


def _where(row):
    return {"area": row["area"], "season": row["season"]}
