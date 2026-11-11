import { useEffect, useState } from "react";
import { api, MonitoringReport } from "./api";

// FR-105: drift and review rejections per area and season. Thresholds are provisional and shown with the data.
const fmt = (v: number | null | undefined, d = 1) => (v === null || v === undefined ? "—" : v.toFixed(d));

export default function Monitoring() {
  const [r, setR] = useState<MonitoringReport | null>(null);
  const [error, setError] = useState("");
  useEffect(() => { api.monitoring().then(setR).catch((e) => setError(e.message)); }, []);
  return (
    <div className="page wide">
      <h1>Monitoring</h1>
      <p className="small muted">How far each area's inputs and outputs are from what the model was trained and checked on, and how often
        reviewers reject its candidates. Signals, not accuracy. <code>python manage.py check_drift</code> records alerts in the audit trail;
        schedule it daily.</p>
      {error && <p className="error">{error}</p>}
      {r && <>
        <h2>Alerts ({r.alerts.length})</h2>
        {r.alerts.length === 0 ? <p className="small">No alerts.</p> :
          r.alerts.map((a, i) => <p key={i} className="badge warn block small"><b>{a.area}, {a.season}</b> · {a.kind.replace(/_/g, " ")}: {a.message}</p>)}
        <h2>By area and season</h2>
        <div className="scroll"><table className="table">
          <thead><tr><th>Area</th><th>Season</th><th>Jobs</th><th>Usable km²</th><th>Largest input shift (SD)</th>
            <th>Candidates km² per 1,000 km²</th><th>Model validation rate</th><th>Ratio</th><th>Reviewed</th><th>Rejected</th></tr></thead>
          <tbody>{r.groups.map((g) => {
            const worst = Object.entries(g.input_shift).sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]))[0];
            return <tr key={g.area + g.season}><td>{g.area}</td><td>{g.season}</td><td>{g.jobs.join(", ")}</td><td>{fmt(g.usable_km2, 0)}</td>
              <td>{worst ? `${worst[0]} ${worst[1] > 0 ? "+" : ""}${worst[1].toFixed(2)}` : "—"}</td>
              <td>{fmt(g.candidate_km2_per_1000km2)}</td><td>{fmt(g.reference_km2_per_1000km2)}</td><td>{fmt(g.output_ratio, 2)}</td>
              <td>{g.reviewed}</td><td>{g.rejection_rate === null ? "—" : `${Math.round(g.rejection_rate * 100)}%`}</td></tr>;
          })}</tbody></table></div>
        <p className="small muted">Provisional rules: input shift over {r.thresholds.input_shift_max_sd} training SD; candidate rate outside{" "}
          {fmt(r.thresholds.output_ratio_range[0], 2)}× to {r.thresholds.output_ratio_range[1]}× the validation rate (with at least{" "}
          {r.thresholds.output_min_usable_km2} km² usable); rejection rate at or above {Math.round(r.thresholds.rejection_rate_max * 100)}% with at least{" "}
          {r.thresholds.rejection_min_reviewed} reviewed.</p>
      </>}
    </div>
  );
}
