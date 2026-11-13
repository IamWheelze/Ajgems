import * as maplibregl from "maplibre-gl";
import { createMap } from "./mapStyle";
import { useEffect, useRef, useState } from "react";
import { api, Me, PoolFeature } from "./api";
import { BASE_STYLE, markReady, whenReady } from "./mapStyle";
import { human } from "./status";

// FR-075: reviewed output becomes a training label only after an independent check. The nominator and
// anyone who reviewed the source cannot check it; the server enforces this, the page explains it.
const STATES = ["nominated", "accepted", "returned"];
const corners = (b: number[]): [[number, number], [number, number], [number, number], [number, number]] =>
  [[b[0], b[3]], [b[2], b[3]], [b[2], b[1]], [b[0], b[1]]];

export default function Pool({ me }: { me: Me }) {
  const mapEl = useRef<HTMLDivElement>(null);
  const map = useRef<maplibregl.Map | null>(null);
  const [items, setItems] = useState<PoolFeature[] | null>(null);
  const [decisions, setDecisions] = useState<Record<string, string[]>>({});
  const [want, setWant] = useState("nominated");
  const [sel, setSel] = useState<PoolFeature | null>(null);
  const [decision, setDecision] = useState(""); const [reason, setReason] = useState(""); const [note, setNote] = useState("");
  const [error, setError] = useState(""); const [busy, setBusy] = useState(false);
  const [tasks, setTasks] = useState<Awaited<ReturnType<typeof api.annotationTasks>>>([]);
  useEffect(() => { if (me.roles.some((r) => ["gis", "ml", "admin"].includes(r))) api.annotationTasks().then(setTasks).catch(() => setTasks([])); }, []);
  const load = () => api.pool().then((r) => { setItems(r.features); setDecisions(r.decisions); }).catch((e) => setError(e.message));
  useEffect(() => { load(); }, []);

  useEffect(() => {
    if (!mapEl.current) return;
    const m = createMap({ container: mapEl.current, style: BASE_STYLE, center: [0, 0], zoom: 2 });
    m.addControl(new maplibregl.NavigationControl({ showCompass: false }));
    m.addControl(new maplibregl.ScaleControl({ unit: "metric" }));
    m.on("load", () => {
      markReady(m); m.setLayoutProperty("osm", "visibility", "none");
      m.addSource("item", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      m.addLayer({ id: "item", type: "line", source: "item", paint: { "line-color": "#ffffff", "line-width": 3 } });
    });
    map.current = m;
    return () => m.remove();
  }, []);

  useEffect(() => {
    const m = map.current;
    if (!m || !sel) return;
    setDecision(""); setReason(""); setNote("");
    whenReady(m, async () => {
      (m.getSource("item") as maplibregl.GeoJSONSource).setData(sel);
      const cs = sel.geometry.coordinates[0] as [number, number][];
      const b = cs.reduce((bb, c) => bb.extend(c), new maplibregl.LngLatBounds(cs[0], cs[0]));
      m.fitBounds(b, { padding: 120, maxZoom: 15, duration: 300 });
      try {
        const { bounds } = await api.poolBounds(sel.properties.id);
        const url = api.poolImageUrl(sel.properties.id);
        const src = m.getSource("img") as maplibregl.ImageSource | undefined;
        if (src) src.updateImage({ url, coordinates: corners(bounds) });
        else {
          m.addSource("img", { type: "image", url, coordinates: corners(bounds) });
          m.addLayer({ id: "img", type: "raster", source: "img", paint: { "raster-resampling": "nearest", "raster-fade-duration": 0 } }, "item");
        }
      } catch (e) { setError((e as Error).message); }
    });
  }, [sel?.properties.id]);

  const shown = (items ?? []).filter((f) => f.properties.state === want);
  const submit = async () => {
    if (!sel) return;
    setBusy(true); setError("");
    try { const f = await api.poolDecide(sel.properties.id, decision, reason, note); setSel(f); await load(); }
    catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  };
  const p = sel?.properties;
  return (
    <div className="split">
      <aside className="panel">
        <h1>Annotation</h1>
        <p className="small muted">Reviewed candidates nominated as training examples. Each becomes a label only after an independent
          check by someone who did not nominate or review it (FR-075). Examples from the Nigerian test regions are never accepted into the pool.</p>
        <div className="row gap">
          {STATES.map((s) => <button key={s} className={want === s ? "primary" : ""} onClick={() => { setWant(s); setSel(null); }}>
            {human(s)} ({(items ?? []).filter((f) => f.properties.state === s).length})</button>)}
        </div>
        {me.roles.includes("ml") && <a className="button" href="/api/pool/export/">Download accepted labels (.zip)</a>}
        <details className="small" open={tasks.length > 0}>
          <summary>Annotation tasks ({tasks.length})</summary>
          <p className="muted">Label a job tile with polygon, brush, split and merge tools (FR-076). Start one from a job's tile with "Annotate this tile".</p>
          <ul className="plain">{tasks.map((t) => <li key={t.id}><a href={`#/annotate/${t.id}`}>Tile {t.tile}</a> · job {t.job} · {t.date} · {t.state} · {t.versions} edit(s)</li>)}</ul>
        </details>
        {error && <p className="error small" role="alert">{error}</p>}
        <ol className="queue" aria-label="Nominations">
          {shown.map((f) => (
            <li key={f.properties.id} className={f.properties.id === p?.id ? "active" : ""} onClick={() => setSel(f)}>
              <span className="grow small"><b>#{f.properties.id} · {f.properties.kind === "mining" ? "Mining" : `Hard negative: ${human(f.properties.negative_type)}`}</b><br />
                <span className="muted">{f.properties.detection ? `candidate #${f.properties.detection}` : `missed site #${f.properties.missed_site}`} · {f.properties.scene_date} · by {f.properties.nominated_by}</span></span>
            </li>
          ))}
          {items !== null && shown.length === 0 && <li className="muted">Nothing here.</li>}
        </ol>
        {p && (
          <div className="card small">
            <b>Example #{p.id}</b>
            <span>{p.kind === "mining" ? "Mining" : `Hard negative (${human(p.negative_type)})`} · source: {p.detection ? `${human(p.status_at_nomination)} candidate` : "missed site"}</span>
            <span className="muted">Scene {p.scene_id} · protocol v{p.protocol_version}</span>
            {p.note && <span>Note: {p.note}</span>}
            {p.decision && <span><b>{human(p.decision.decision)}</b> by {p.decision.checker}: {human(p.decision.reason_code)}{p.decision.note && ` (${p.decision.note})`}</span>}
            {p.state === "nominated" && (p.can_check ? (
              <>
                <div className="actions">{Object.keys(decisions).map((d) => <button key={d} className={decision === d ? "primary" : ""} onClick={() => { setDecision(d); setReason(""); }}>{d === "accept" ? "Accept as label" : "Return"}</button>)}</div>
                {decision && <>
                  <label>Reason<select value={reason} onChange={(e) => setReason(e.target.value)}><option value="">Choose…</option>
                    {decisions[decision].map((r) => <option key={r} value={r}>{human(r)}</option>)}</select></label>
                  <label>Note{reason === "other" && " (required)"}<textarea rows={2} value={note} onChange={(e) => setNote(e.target.value)} /></label>
                  <button className="primary" disabled={busy || !reason || (reason === "other" && !note.trim())} onClick={submit}>Record decision</button>
                </>}
              </>
            ) : <span className="badge warn">You nominated this or reviewed its source, so someone else must check it.</span>)}
          </div>
        )}
      </aside>
      <div className="err-map"><div ref={mapEl} className="map" /></div>
    </div>
  );
}
