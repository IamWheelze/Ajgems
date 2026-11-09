import { useEffect, useState } from "react";
import { api, Job, Me } from "./api";
import { STATUS } from "./status";

// Export and report (FR-106 to FR-108): data, PDF report with coverage map and limits,
// licence notice and checksummed manifest in one zip. Viewers can export released results only.
export default function ExportPanel({ projectId, me }: { projectId: number; me: Me }) {
  const viewerOnly = me.roles.includes("viewer") && !me.roles.some((r) => r === "gis" || r === "admin");
  const [format, setFormat] = useState("gpkg");
  const [statuses, setStatuses] = useState<string[]>(Object.keys(STATUS).filter((s) => s !== "rejected"));
  const [jobs, setJobs] = useState<Job[]>([]);
  const [job, setJob] = useState<number | "">("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => { if (!viewerOnly) api.jobs(projectId).then(setJobs).catch(() => setJobs([])); }, [projectId, viewerOnly]);

  const toggle = (s: string) => setStatuses((xs) => xs.includes(s) ? xs.filter((x) => x !== s) : [...xs, s]);
  const go = async () => {
    setBusy(true); setError("");
    try { await api.exportPackage(projectId, format, viewerOnly ? ["released"] : statuses, job === "" ? undefined : job); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  };

  return (
    <div className="card">
      <h3>Export and report</h3>
      <label className="small">Format
        <select value={format} onChange={(e) => setFormat(e.target.value)}>
          <option value="gpkg">GeoPackage</option><option value="geojson">GeoJSON</option><option value="csv">CSV (WKT geometry)</option>
        </select>
      </label>
      {viewerOnly ? <p className="small muted">Released candidates only.</p> : (
        <>
          <fieldset className="small statuses"><legend>Review status</legend>
            {Object.entries(STATUS).map(([s, x]) => (
              <label key={s} className="check"><input type="checkbox" checked={statuses.includes(s)} onChange={() => toggle(s)} />{x.icon} {x.label}</label>
            ))}
          </fieldset>
          {jobs.length > 1 && (
            <label className="small">Job
              <select value={job} onChange={(e) => setJob(e.target.value === "" ? "" : Number(e.target.value))}>
                <option value="">Latest finished job per area</option>
                {jobs.map((j) => <option key={j.id} value={j.id}>Job {j.id} · {j.created_at.slice(0, 10)} · {j.candidates} candidates</option>)}
              </select>
            </label>
          )}
        </>
      )}
      <p className="small muted">The zip holds the data, a PDF report (coverage map, method, limits, versions, evidence grades), the
        licence notice and a manifest of checksums.</p>
      <button className="primary" onClick={go} disabled={busy || (!viewerOnly && statuses.length === 0)}>{busy ? "Preparing…" : "Download package"}</button>
      {error && <p className="error small" role="alert">{error}</p>}
    </div>
  );
}
