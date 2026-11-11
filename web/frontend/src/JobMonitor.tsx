import maplibregl from "maplibre-gl";
import { useEffect, useRef, useState } from "react";
import { api, Job, Me, TileProps } from "./api";
import { BASE_STYLE, markReady, whenReady } from "./mapStyle";
import { ACTIVE_JOB, human, JOB_STATE, OUTCOME } from "./status";

// Diagonal hatch for "insufficient usable imagery": never blank, never clean ground (FRD 10.3).
function hatchImage(): ImageData {
  const n = 12, c = document.createElement("canvas");
  c.width = c.height = n;
  const g = c.getContext("2d")!;
  g.strokeStyle = "rgba(200,200,200,0.9)"; g.lineWidth = 2;
  g.beginPath(); g.moveTo(0, n); g.lineTo(n, 0); g.moveTo(-n / 2, n / 2); g.lineTo(n / 2, -n / 2); g.moveTo(n / 2, n * 1.5); g.lineTo(n * 1.5, n / 2); g.stroke();
  return g.getImageData(0, 0, n, n);
}

// FR-126 preview: what changes at other settings. Colour, line style and label together, never colour alone.
const CHANGE: Record<string, { label: string; colour: string; dash?: number[] }> = {
  kept: { label: "Kept", colour: "#0072B2" }, added: { label: "Added", colour: "#E69F00", dash: [3, 2] },
  removed: { label: "Removed", colour: "#D55E00", dash: [1, 2] },
};

const mins = (s: number) => (s < 90 ? `${Math.round(s)} s` : `${(s / 60).toFixed(1)} min`);

export default function JobMonitor({ projectId, jobId, me }: { projectId: number; jobId: number; me: Me }) {
  const mapEl = useRef<HTMLDivElement>(null);
  const map = useRef<maplibregl.Map | null>(null);
  const fitted = useRef(false);
  const [job, setJob] = useState<Job | null>(null);
  const [tiles, setTiles] = useState<GeoJSON.FeatureCollection<GeoJSON.Polygon, TileProps> | null>(null);
  const [picked, setPicked] = useState<TileProps | null>(null);
  const [error, setError] = useState("");
  const canAct = me.roles.includes("gis");
  const [pv, setPv] = useState<{ threshold: number; mmu: number } | null>(null);
  const [preview, setPreview] = useState<Awaited<ReturnType<typeof api.jobPreview>> | null>(null);
  const [pvBusy, setPvBusy] = useState(false);
  useEffect(() => { if (job && !pv) setPv({ threshold: Number(job.parameters.threshold), mmu: Number(job.parameters.mmu_ha) }); }, [job, pv]);
  const runPreview = async () => {
    if (!pv) return;
    setPvBusy(true); setError("");
    try { setPreview(await api.jobPreview(jobId, pv.threshold, pv.mmu)); } catch (e) { setError((e as Error).message); }
    finally { setPvBusy(false); }
  };
  const savePreview = async () => {
    if (!pv || !confirm(`Save threshold ${pv.threshold.toFixed(2)} and minimum ${pv.mmu} ha as a new job? Job ${jobId}, its candidates and reviews stay as they are.`)) return;
    setPvBusy(true);
    try { const j = await api.jobRethreshold(jobId, pv.threshold, pv.mmu); location.hash = `#/projects/${projectId}/jobs/${j.id}`; }
    catch (e) { setError((e as Error).message); } finally { setPvBusy(false); }
  };
  useEffect(() => {
    const m = map.current;
    if (!m) return;
    whenReady(m, () => {
      if (!m.getSource("preview")) {
        m.addSource("preview", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
        for (const [k, c] of Object.entries(CHANGE))
          m.addLayer({ id: `preview-${k}`, type: "line", source: "preview", filter: ["==", ["get", "change"], k],
            paint: { "line-color": c.colour, "line-width": 2, ...(c.dash ? { "line-dasharray": c.dash } : {}) } });
      }
      (m.getSource("preview") as maplibregl.GeoJSONSource).setData(preview ?? { type: "FeatureCollection", features: [] });
    });
  }, [preview]);

  const load = () => Promise.all([api.job(jobId), api.jobTiles(jobId)]).then(([j, t]) => { setJob(j); setTiles(t); })
    .catch((e) => setError(e.message));
  useEffect(() => { load(); }, [jobId]);
  useEffect(() => {   // poll while the job is active (FR-085)
    if (!job || !ACTIVE_JOB.has(job.state)) return;
    const t = setInterval(load, 3000);
    return () => clearInterval(t);
  }, [job?.state]);

  useEffect(() => {
    if (!mapEl.current) return;
    const m = new maplibregl.Map({ container: mapEl.current, style: BASE_STYLE, center: [0, 0], zoom: 2 });
    m.addControl(new maplibregl.NavigationControl({ showCompass: false }));
    m.addControl(new maplibregl.ScaleControl({ unit: "metric" }));
    m.on("load", () => {
      markReady(m);
      m.addImage("hatch", hatchImage());
      m.addSource("tiles", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      const colour = ["match", ["get", "outcome"],
        ...Object.entries(OUTCOME).flatMap(([k, v]) => [k, v.colour]), "#555555"] as unknown as maplibregl.ExpressionSpecification;
      m.addLayer({ id: "tiles-fill", type: "fill", source: "tiles", filter: ["!=", ["get", "outcome"], "insufficient_usable_imagery"],
        paint: { "fill-color": colour, "fill-opacity": ["match", ["get", "state"], "done", 0.35, 0.12] } });
      m.addLayer({ id: "tiles-hatch", type: "fill", source: "tiles", filter: ["==", ["get", "outcome"], "insufficient_usable_imagery"],
        paint: { "fill-pattern": "hatch" } });
      m.addLayer({ id: "tiles-line", type: "line", source: "tiles", paint: { "line-color": colour, "line-width": 1.5,
        "line-dasharray": [1, 0] } });
      m.addLayer({ id: "tiles-active", type: "line", source: "tiles",
        filter: ["in", ["get", "state"], ["literal", ["fetching", "checking", "running"]]],
        paint: { "line-color": "#ffffff", "line-width": 3 } });
      m.on("click", "tiles-fill", (e) => setPicked(e.features?.[0]?.properties as unknown as TileProps));
      m.on("click", "tiles-hatch", (e) => setPicked(e.features?.[0]?.properties as unknown as TileProps));
    });
    map.current = m;
    return () => m.remove();
  }, []);

  useEffect(() => {
    const m = map.current;
    if (!m || !tiles) return;
    whenReady(m, () => {
      (m.getSource("tiles") as maplibregl.GeoJSONSource).setData(tiles);
      if (!fitted.current && tiles.features.length) {
        const b = new maplibregl.LngLatBounds();
        tiles.features.forEach((f) => f.geometry.coordinates[0].forEach((c) => b.extend(c as [number, number])));
        m.fitBounds(b, { padding: 40, duration: 0 }); fitted.current = true;
      }
      m.setLayoutProperty("osm", "visibility", "visible");
    });
  }, [tiles]);

  const act = async (a: "cancel" | "resume") => {
    setError("");
    try { setJob(await api.jobAction(jobId, a)); load(); } catch (e) { setError((e as Error).message); }
  };

  const done = job ? (job.tiles.by_state.done ?? 0) + (job.tiles.by_state.skipped ?? 0) + (job.tiles.by_state.failed ?? 0) : 0;
  return (
    <div className="split">
      <aside className="panel">
        <a href={`#/projects/${projectId}`} className="small">← Project</a>
        <h1>Job {jobId}</h1>
        {job && (
          <>
            <p><span className={`badge ${job.state.includes("warn") || job.state === "failed" || job.state === "blocked_by_quality" ? "warn" : ""}`}>
              {JOB_STATE[job.state] ?? job.state}</span> {ACTIVE_JOB.has(job.state) && <span className="small muted">updating…</span>}</p>
            <progress max={job.tiles.total} value={done} style={{ width: "100%" }} />
            <p className="small">{done} of {job.tiles.total} tiles · {job.candidates} candidate footprints</p>
            {job.progress && job.started_at && (   // FR-132: time and data so far
              <p className="small muted">{ACTIVE_JOB.has(job.state) ? "Running for" : "Took"} {mins(job.progress.elapsed_s)}
                {job.progress.remaining_s !== null && ` · about ${mins(job.progress.remaining_s)} left`}
                {job.progress.bytes_fetched > 0 && ` · ${(job.progress.bytes_fetched / 1e6).toFixed(1)} MB imagery fetched`}
                {` · compute: ${job.progress.compute}, no imagery charges`}</p>
            )}
            {job.message && <p className="small">{job.message}</p>}
            <dl className="facts small">
              <dt>Model</dt><dd>{job.model_version}</dd>
              <dt>Dates</dt><dd>{String(job.parameters.start)} to {String(job.parameters.end)}</dd>
              <dt>Threshold</dt><dd>{Number(job.parameters.threshold).toFixed(2)} (model score)</dd>
              <dt>Min. unit</dt><dd>{String(job.parameters.mmu_ha)} ha</dd>
            </dl>
            {job.estimate.model?.domain_warning && <p className="badge warn block small">{job.estimate.model.domain_warning}</p>}
            {job.progress?.area_coverage?.usable_pct != null && (   // FR-051 per area
              <p className="small">Usable imagery over the area: <b>{job.progress.area_coverage.usable_pct}%</b>
                {" "}· not assessed: <b>{job.progress.area_coverage.not_assessed_pct}%</b></p>
            )}
            <h3>Tile outcomes</h3>
            <ul className="plain small legend-list">
              {Object.entries(OUTCOME).map(([k, v]) => (
                <li key={k}><span className={`swatch ${v.hatch ? "hatched" : ""}`} style={{ background: v.hatch ? undefined : v.colour }} />
                  {v.label}: <b>{job.tiles.by_outcome[k] ?? 0}</b></li>
              ))}
            </ul>
            <div className="actions wrap">
              {canAct && ACTIVE_JOB.has(job.state) && job.state !== "cancelling" && <button onClick={() => act("cancel")}>Cancel</button>}
              {canAct && (job.state === "resumable" || (job.state === "cancelled" && job.started_at)) &&
                <button onClick={() => act("resume")}>Resume</button>}
              {job.candidates > 0 && !ACTIVE_JOB.has(job.state) &&
                <a className="button primary" href={`#/projects/${projectId}/review`}>Review candidates</a>}
            </div>
          </>
        )}
        {job && ["succeeded", "succeeded_with_warnings"].includes(job.state) && pv && (
          <div className="card small">
            <h3>Threshold and minimum size preview</h3>
            <p className="muted">Recomputed from this job's stored score maps. Nothing is saved until you choose to.</p>
            <label>Threshold (model score): <b>{pv.threshold.toFixed(2)}</b>
              <input type="range" min="0.05" max="0.95" step="0.01" value={pv.threshold} onChange={(e) => setPv({ ...pv, threshold: Number(e.target.value) })} /></label>
            <label>Minimum size, ha<input type="number" min="0.1" step="0.5" value={pv.mmu} onChange={(e) => setPv({ ...pv, mmu: Number(e.target.value) })} /></label>
            <div className="actions wrap">
              <button className="primary" disabled={pvBusy} onClick={runPreview}>{pvBusy ? "Working…" : "Preview"}</button>
              {preview && <button onClick={() => setPreview(null)}>Clear</button>}
            </div>
            {preview && (
              <>
                <ul className="plain legend-list">
                  {Object.entries(CHANGE).map(([k, c]) => <li key={k}><svg width="22" height="8"><line x1="0" y1="4" x2="22" y2="4" stroke={c.colour} strokeWidth="2"
                    strokeDasharray={c.dash?.map((d) => d * 2).join(" ")} /></svg> {c.label}: <b>{preview.summary[k as "kept" | "added" | "removed"]}</b></li>)}
                </ul>
                <span>{preview.summary.candidates} candidates, {preview.summary.area_ha.toLocaleString()} ha (job: {preview.summary.job_candidates})</span>
                {canAct && <button disabled={pvBusy} onClick={savePreview}>Save as a new job</button>}
              </>
            )}
          </div>
        )}
        {picked && (
          <div className="card small">
            <b>Tile {picked.grid_id}</b>
            <span>{OUTCOME[picked.outcome]?.label ?? picked.outcome} · {human(picked.state)}</span>
            {picked.scene && <span>Scene {picked.scene} ({picked.date})</span>}
            {picked.usable_pct !== null && <span>Usable imagery {Number(picked.usable_pct).toFixed(1)}% ({picked.quality_decision})</span>}
            <span>{picked.candidates} candidate piece(s)</span>
            {picked.failure_reason && <span className="error">{picked.failure_reason}</span>}
            {picked.checksums && Object.entries(picked.checksums).map(([f, h]) => <span key={f} className="muted" title={h}>{f} SHA-256 {h.slice(0, 12)}…</span>)}
          </div>
        )}
        {error && <p className="error small" role="alert">{error}</p>}
      </aside>
      <div ref={mapEl} className="map" />
    </div>
  );
}
