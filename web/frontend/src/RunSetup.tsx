import { useEffect, useState } from "react";
import { api, Area, Job } from "./api";
import { JOB_STATE } from "./status";

// Run setup (FRD 10.4): estimate first, show the model card and its limits, then start.
export default function RunSetup({ projectId, areas }: { projectId: number; areas: Area[] }) {
  const [area, setArea] = useState<number | "">("");
  const [start, setStart] = useState("2025-12-01");
  const [end, setEnd] = useState("2026-02-28");
  const [cloud, setCloud] = useState(20);
  const [job, setJob] = useState<Job | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => { api.jobs(projectId).then(setJobs).catch(() => setJobs([])); }, [projectId]);
  useEffect(() => { if (area === "" && areas.length) setArea(areas[0].id); }, [areas, area]);

  const estimate = async () => {
    if (area === "") return;
    setBusy(true); setError(""); setJob(null);
    try { setJob(await api.estimateJob(projectId, area, start, end, cloud)); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  };
  const go = async () => {
    if (!job) return;
    setBusy(true);
    try { await api.jobAction(job.id, "start"); window.location.hash = `#/projects/${projectId}/jobs/${job.id}`; }
    catch (e) { setError((e as Error).message); setBusy(false); }
  };

  const m = job?.estimate.model;
  return (
    <div className="card">
      <h3>Run detection</h3>
      {areas.length === 0 ? <p className="small muted">Add an area first.</p> : (
        <>
          <label className="small">Area
            <select value={area} onChange={(e) => { setArea(Number(e.target.value)); setJob(null); }}>
              {areas.map((a) => <option key={a.id} value={a.id}>{a.name} ({a.area_km2.toFixed(0)} km²)</option>)}
            </select>
          </label>
          <div className="row gap">
            <label className="small grow">From<input type="date" value={start} onChange={(e) => { setStart(e.target.value); setJob(null); }} /></label>
            <label className="small grow">To<input type="date" value={end} onChange={(e) => { setEnd(e.target.value); setJob(null); }} /></label>
          </div>
          <label className="small">Maximum scene cloud: {cloud}%
            <input type="range" min={0} max={60} value={cloud} onChange={(e) => { setCloud(Number(e.target.value)); setJob(null); }} />
          </label>
          <button onClick={estimate} disabled={busy || area === ""}>{busy && !job ? "Estimating…" : "Estimate"}</button>
        </>
      )}
      {job && m && (
        <div className="estimate small">
          <p><b>{job.estimate.tiles}</b> tiles ({job.estimate.tiles_with_imagery} with imagery) · {job.estimate.scenes_found} scene(s)
            found · clearest {job.estimate.clearest_cloud_pct?.toFixed(1)}% cloud · about <b>{job.estimate.estimated_minutes} min</b>
            {job.estimate.source && <> · source <b>{job.estimate.source}</b></>}
            {job.estimate.sources_not_approved && Object.keys(job.estimate.sources_not_approved).length > 0 &&
              <span className="small muted"> (not used for this project's intended use: {Object.keys(job.estimate.sources_not_approved).join(", ")})</span>}
            <span className="muted"> ({job.estimate.estimate_basis})</span></p>
          <p>Model <b>{m.model_version}</b> · threshold {m.threshold.toFixed(2)} · {m.score_type}, not calibrated
            {m.global_test_f1 !== undefined && <> · global test pixel F1 {m.global_test_f1.toFixed(2)}</>}</p>
          {m.domain_warning && <p className="badge warn block">{m.domain_warning}</p>}
          <button className="primary" onClick={go} disabled={busy}>Start job</button>
        </div>
      )}
      {error && <p className="error small" role="alert">{error}</p>}
      {jobs.length > 0 && (
        <>
          <h3>Jobs</h3>
          <ul className="plain small">
            {jobs.map((j) => <li key={j.id}><a href={`#/projects/${projectId}/jobs/${j.id}`}>Job {j.id}</a> · {JOB_STATE[j.state] ?? j.state}
              · {j.candidates} candidates · {j.created_at.slice(0, 10)}</li>)}
          </ul>
        </>
      )}
    </div>
  );
}
