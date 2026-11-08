import maplibregl from "maplibre-gl";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, DetectionFeature, Me, Project, ReviewOptions } from "./api";
import { BASE_STYLE, markReady, whenReady } from "./mapStyle";
import { ACTION_LABEL, BAND_LABEL, human, SHORTCUTS, STATUS } from "./status";

const LAYERS: Record<string, string> = {
  true_colour: "True colour (B4 B3 B2)", swir_false_colour: "SWIR false colour (B12 B8A B4)",
  nir_false_colour: "NIR false colour (B8 B4 B3)", quality: "Quality mask (not assessed areas)",
};
const BAND_ORDER: Record<string, number> = { high: 0, medium: 1, low: 2 };

export default function ReviewWorkspace({ projectId, me }: { projectId: number; me: Me }) {
  const mapEl = useRef<HTMLDivElement>(null);
  const map = useRef<maplibregl.Map | null>(null);
  const [project, setProject] = useState<Project | null>(null);
  const [features, setFeatures] = useState<DetectionFeature[]>([]);
  const [options, setOptions] = useState<ReviewOptions>({});
  const [filter, setFilter] = useState("unreviewed");
  const [selected, setSelected] = useState<DetectionFeature | null>(null);
  const [layer, setLayer] = useState("true_colour");
  const [showOsm, setShowOsm] = useState(false);
  const [imagery, setImagery] = useState<{ date: string; scene_id: string; licence_note: string } | null>(null);
  const [action, setAction] = useState("");
  const [reason, setReason] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    Promise.all([api.project(projectId), api.detections(projectId), api.reviewOptions()])
      .then(([p, fc, o]) => { setProject(p); setFeatures(fc.features as DetectionFeature[]); setOptions(o); })
      .catch((e) => setError(e.message));
  }, [projectId]);

  const queue = useMemo(() => features
    .filter((f) => filter === "all" || f.properties.status === filter)
    .sort((a, b) => BAND_ORDER[a.properties.score_band] - BAND_ORDER[b.properties.score_band] || b.properties.area_ha - a.properties.area_ha),
  [features, filter]);
  const reviewed = features.filter((f) => f.properties.status !== "unreviewed").length;
  const counts = useMemo(() => features.reduce<Record<string, number>>((c, f) => ({ ...c, [f.properties.status]: (c[f.properties.status] ?? 0) + 1 }), {}), [features]);

  const select = useCallback((f: DetectionFeature | null) => {
    setAction(""); setReason(""); setNote(""); setError("");
    if (!f) { setSelected(null); return; }
    setSelected(f);
    api.detection(f.properties.id).then(setSelected).catch((e) => setError(e.message));
  }, []);

  useEffect(() => { if (!selected && queue.length) select(queue[0]); }, [queue, selected, select]);

  // Map setup: one line layer per status so each status has its own dash pattern (MapLibre dashes are not data-driven).
  useEffect(() => {
    if (!mapEl.current) return;
    const m = new maplibregl.Map({ container: mapEl.current, style: BASE_STYLE, center: [0, 0], zoom: 2 });
    m.addControl(new maplibregl.NavigationControl({ showCompass: false }));
    m.addControl(new maplibregl.ScaleControl({ unit: "metric" }));
    m.on("load", () => {
      markReady(m);
      m.setLayoutProperty("osm", "visibility", "none");
      m.addSource("dets", { type: "geojson", data: { type: "FeatureCollection", features: [] }, promoteId: "id" });
      m.addLayer({ id: "dets-fill", type: "fill", source: "dets", paint: { "fill-color": "#ffffff", "fill-opacity": 0.04 } });
      for (const [s, st] of Object.entries(STATUS)) {
        m.addLayer({ id: `dets-${s}`, type: "line", source: "dets", filter: ["==", ["get", "status"], s],
          paint: { "line-color": st.colour, "line-width": 2, ...(st.dash ? { "line-dasharray": st.dash } : {}) } });
      }
      m.addLayer({ id: "dets-selected", type: "line", source: "dets", filter: ["==", ["get", "id"], -1],
        paint: { "line-color": "#ffffff", "line-width": 4 } });
      m.on("click", "dets-fill", (e) => {
        const id = e.features?.[0]?.properties?.id;
        const f = featuresRef.current.find((x) => x.properties.id === id);
        if (f) select(f);
      });
      m.on("mouseenter", "dets-fill", () => (m.getCanvas().style.cursor = "pointer"));
      m.on("mouseleave", "dets-fill", () => (m.getCanvas().style.cursor = ""));
    });
    map.current = m;
    return () => m.remove();
  }, [select]);

  const featuresRef = useRef(features);
  useEffect(() => {
    featuresRef.current = features;
    const m = map.current;
    const apply = () => (m!.getSource("dets") as maplibregl.GeoJSONSource)?.setData({
      type: "FeatureCollection", features: features.map((f) => ({ ...f, properties: { id: f.properties.id, status: f.properties.status } })) });
    if (m) whenReady(m, apply);
  }, [features]);

  useEffect(() => {
    const m = map.current;
    if (m && m.getLayer("osm")) m.setLayoutProperty("osm", "visibility", showOsm ? "visible" : "none");
  }, [showOsm]);

  // Show the selected candidate's own scene, dated, under the polygons.
  useEffect(() => {
    const m = map.current;
    if (!m || !selected) return;
    const sceneId = selected.properties.scene.id;
    const run = async () => {
      m.setFilter("dets-selected", ["==", ["get", "id"], selected.properties.id]);
      const coords = selected.geometry.coordinates[0] as [number, number][];
      const b = coords.reduce((bb, c) => bb.extend(c), new maplibregl.LngLatBounds(coords[0], coords[0]));
      m.fitBounds(b, { padding: 120, maxZoom: 15, duration: 300 });
      try {
        const meta = await api.sceneBounds(sceneId, layer);
        setImagery(meta);
        const [w, s, e, n] = meta.bounds;
        const corners: [[number, number], [number, number], [number, number], [number, number]] = [[w, n], [e, n], [e, s], [w, s]];
        const url = api.sceneImageUrl(sceneId, layer);
        const src = m.getSource("scene") as maplibregl.ImageSource | undefined;
        if (src) src.updateImage({ url, coordinates: corners });
        else {
          m.addSource("scene", { type: "image", url, coordinates: corners });
          m.addLayer({ id: "scene", type: "raster", source: "scene", paint: { "raster-resampling": "nearest", "raster-fade-duration": 0 } }, "dets-fill");
        }
      } catch (e) { setError((e as Error).message); }
    };
    whenReady(m, run);
  }, [selected?.properties.id, selected?.properties.scene.id, layer]);

  const allowed = useMemo(() => {
    if (!selected) return [];
    return Object.entries(options).filter(([a, o]) => a !== "edit" && o.roles.some((r) => me.roles.includes(r))
      && (!o.allowed_from || o.allowed_from.includes(selected.properties.status))).map(([a]) => a);
  }, [options, selected, me.roles]);

  const submit = useCallback(async () => {
    if (!selected || !action || !reason) return;
    setBusy(true); setError("");
    try {
      const updated = await api.review(selected.properties.id, action, reason, note);
      setFeatures((fs) => fs.map((f) => (f.properties.id === updated.properties.id ? updated : f)));
      const idx = queue.findIndex((f) => f.properties.id === selected.properties.id);
      const next = queue.filter((f) => f.properties.id !== selected.properties.id)[Math.max(0, idx)] ?? null;
      if (filter === "all" || updated.properties.status === filter) setSelected(updated); else select(next);
      setAction(""); setReason(""); setNote("");
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }, [selected, action, reason, note, queue, filter, select]);

  // Keyboard review: c/r/u/f choose an action, j/k move through the queue, Enter submits.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement).closest("input, textarea, select")) return;
      const i = queue.findIndex((f) => f.properties.id === selected?.properties.id);
      if (e.key === "j" && i < queue.length - 1) select(queue[i + 1]);
      else if (e.key === "k" && i > 0) select(queue[i - 1]);
      else if (SHORTCUTS[e.key] && allowed.includes(SHORTCUTS[e.key])) { setAction(SHORTCUTS[e.key]); setReason(""); }
      else if (e.key === "Enter") submit();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [queue, selected, allowed, submit, select]);

  const p = selected?.properties;
  const st = p ? STATUS[p.status] : null;
  return (
    <div className="workspace">
      <div className="ws-top">
        <a href={`#/projects/${projectId}`} className="small">← {project?.name ?? "Project"}</a>
        <span>Imagery: <b>{imagery ? `Sentinel-2, ${imagery.date.slice(0, 10)}` : "—"}</b></span>
        <span>Model: <b>{p?.model_version ?? "—"}</b></span>
        <span className="badge" title="Scores are not calibrated probabilities (FR-084)">Model score, not calibrated</span>
        <span className="spacer" />
        <span className="small muted">{reviewed} of {features.length} reviewed</span>
      </div>

      <aside className="ws-queue">
        <label className="small">Show
          <select value={filter} onChange={(e) => { setFilter(e.target.value); setSelected(null); }}>
            <option value="all">All ({features.length})</option>
            {Object.entries(STATUS).map(([s, x]) => <option key={s} value={s}>{x.icon} {x.label} ({counts[s] ?? 0})</option>)}
          </select>
        </label>
        <ol className="queue" aria-label="Review queue">
          {queue.map((f) => {
            const s = STATUS[f.properties.status];
            return (
              <li key={f.properties.id} className={f.properties.id === p?.id ? "active" : ""} onClick={() => select(f)}>
                <span className="icon" style={{ color: s.colour }} aria-label={s.label}>{s.icon}</span>
                <span className="grow">#{f.properties.id} · {f.properties.area_ha.toFixed(1)} ha</span>
                <span className={`band band-${f.properties.score_band}`}>{BAND_LABEL[f.properties.score_band]}</span>
              </li>
            );
          })}
          {queue.length === 0 && <li className="muted">Nothing in this view.</li>}
        </ol>
        <p className="small muted">Keys: c confirm · r reject · u uncertain · f field check · j/k next/previous · Enter submit</p>
      </aside>

      <div className="ws-map">
        <div ref={mapEl} className="map" />
        <div className="map-overlay">
          <select value={layer} onChange={(e) => setLayer(e.target.value)} aria-label="Band preset">
            {Object.entries(LAYERS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
          <label className="small"><input type="checkbox" checked={showOsm} onChange={(e) => setShowOsm(e.target.checked)} /> OpenStreetMap (orientation only)</label>
          {imagery && <div className="small">{imagery.scene_id}<br />{imagery.licence_note}</div>}
        </div>
        <div className="legend small">
          {Object.entries(STATUS).map(([s, x]) => (
            <span key={s}><svg width="22" height="8"><line x1="0" y1="4" x2="22" y2="4" stroke={x.colour} strokeWidth="2"
              strokeDasharray={x.dash ? x.dash.map((d) => d * 2).join(" ") : undefined} /></svg> {x.icon} {x.label}</span>
          ))}
        </div>
      </div>

      <aside className="ws-evidence">
        {!p ? <p className="muted">Select a candidate.</p> : (
          <>
            <h2><span style={{ color: st!.colour }}>{st!.icon}</span> Candidate #{p.id}</h2>
            <p className="small">Status: <b>{st!.label}</b> · Evidence grade {p.evidence_grade}</p>
            <dl className="facts">
              <dt>Area</dt><dd>{p.area_ha.toFixed(1)} ha</dd>
              <dt>Model score</dt><dd><span className={`band band-${p.score_band}`}>{BAND_LABEL[p.score_band]}</span> <span className="small muted">(band, not a probability)</span></dd>
              <dt>Scene</dt><dd className="small">{p.scene.source_id}</dd>
              <dt>Date</dt><dd>{p.scene.acquired_at.slice(0, 10)}</dd>
              <dt>Not assessed</dt><dd>{(p.masked_fraction * 100).toFixed(1)}% of polygon {p.mask_status !== "assessed" && <span className="badge warn">{human(p.mask_status)}</span>}</dd>
              <dt>Model</dt><dd className="small">{p.model_version}</dd>
              <dt>Settings</dt><dd className="small">threshold {p.threshold.toFixed(2)} · min. unit {p.min_mapping_unit_ha} ha</dd>
            </dl>
            <p className="statement small">{p.statement}</p>

            {allowed.length > 0 && (
              <div className="card decide">
                <h3>Decision</h3>
                <div className="actions wrap">
                  {allowed.map((a) => (
                    <button key={a} className={action === a ? "primary" : ""} onClick={() => { setAction(a); setReason(""); }}>{ACTION_LABEL[a] ?? human(a)}</button>
                  ))}
                </div>
                {action && (
                  <>
                    <label className="small">Reason (required)
                      <select value={reason} onChange={(e) => setReason(e.target.value)} autoFocus>
                        <option value="">Choose…</option>
                        {options[action].reasons.map((r) => <option key={r} value={r}>{human(r)}</option>)}
                      </select>
                    </label>
                    <label className="small">Note{reason === "other" && " (required)"}<textarea rows={2} value={note} onChange={(e) => setNote(e.target.value)} /></label>
                    <button className="primary" disabled={busy || !reason || (reason === "other" && !note.trim())} onClick={submit}>
                      {busy ? "Saving…" : `Record ${ACTION_LABEL[action] ?? human(action)}`}
                    </button>
                  </>
                )}
              </div>
            )}
            {error && <p className="error" role="alert">{error}</p>}

            <h3>History</h3>
            {(p.reviews ?? []).length === 0 ? <p className="small muted">No decisions yet.</p> : (
              <ol className="history small">
                {p.reviews!.map((r, i) => <li key={i}><b>{ACTION_LABEL[r.action] ?? r.action}</b> · {human(r.reason_code)} · {r.reviewer_role} · {r.at.slice(0, 16).replace("T", " ")}{r.note && <div className="muted">{r.note}</div>}</li>)}
              </ol>
            )}
          </>
        )}
      </aside>
    </div>
  );
}
