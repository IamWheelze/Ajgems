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

export default function JobMonitor({ projectId, jobId, me }: { projectId: number; jobId: number; me: Me }) {
  const mapEl = useRef<HTMLDivElement>(null);
  const map = useRef<maplibregl.Map | null>(null);
  const fitted = useRef(false);
  const [job, setJob] = useState<Job | null>(null);
  const [tiles, setTiles] = useState<GeoJSON.FeatureCollection<GeoJSON.Polygon, TileProps> | null>(null);
  const [picked, setPicked] = useState<TileProps | null>(null);
  const [error, setError] = useState("");
  const canAct = me.roles.includes("gis");

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
            {job.message && <p className="small">{job.message}</p>}
            <dl className="facts small">
              <dt>Model</dt><dd>{job.model_version}</dd>
              <dt>Dates</dt><dd>{String(job.parameters.start)} to {String(job.parameters.end)}</dd>
              <dt>Threshold</dt><dd>{Number(job.parameters.threshold).toFixed(2)} (model score)</dd>
              <dt>Min. unit</dt><dd>{String(job.parameters.mmu_ha)} ha</dd>
            </dl>
            {job.estimate.model?.domain_warning && <p className="badge warn block small">{job.estimate.model.domain_warning}</p>}
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
        {picked && (
          <div className="card small">
            <b>Tile {picked.grid_id}</b>
            <span>{OUTCOME[picked.outcome]?.label ?? picked.outcome} · {human(picked.state)}</span>
            {picked.scene && <span>Scene {picked.scene} ({picked.date})</span>}
            {picked.usable_pct !== null && <span>Usable imagery {Number(picked.usable_pct).toFixed(1)}% ({picked.quality_decision})</span>}
            <span>{picked.candidates} candidate piece(s)</span>
            {picked.failure_reason && <span className="error">{picked.failure_reason}</span>}
          </div>
        )}
        {error && <p className="error small" role="alert">{error}</p>}
      </aside>
      <div ref={mapEl} className="map" />
    </div>
  );
}
