import * as maplibregl from "maplibre-gl";
import { createMap } from "./mapStyle";
import { useEffect, useRef, useState } from "react";
import { api, AnnotationTask } from "./api";
import { BASE_STYLE, markReady, whenReady } from "./mapStyle";
import { human } from "./status";

// FR-076: label a job tile on its own imagery with polygon, brush, eraser, split, merge and delete tools.
// Edits are computed and validated on the server in metres; each is an undoable, immutable version.
// Model candidates are not shown here, so labels are not pulled towards the model.
const CLASS_STYLE: Record<string, { colour: string; dash?: number[] }> = {
  mining: { colour: "#D55E00" }, farmland: { colour: "#009E73", dash: [3, 2] }, settlement: { colour: "#CC79A7", dash: [3, 2] },
  riverbank: { colour: "#56B4E9", dash: [3, 2] }, bare_ground: { colour: "#E69F00", dash: [1, 2] },
  quarry_or_borrow_pit: { colour: "#0072B2", dash: [4, 2, 1, 2] }, burn_scar: { colour: "#F0E442", dash: [1, 2] },
  other_land_use: { colour: "#BBBBBB", dash: [1, 2] },
};
type Tool = "polygon" | "brush" | "erase" | "split" | "merge" | "delete";
const TOOLS: [Tool, string][] = [["polygon", "Polygon"], ["brush", "Brush"], ["erase", "Eraser"], ["split", "Split"], ["merge", "Merge"], ["delete", "Delete"]];
const corners = (b: number[]): [[number, number], [number, number], [number, number], [number, number]] =>
  [[b[0], b[3]], [b[2], b[3]], [b[2], b[1]], [b[0], b[1]]];

export default function Annotate({ taskId }: { taskId: number }) {
  const mapEl = useRef<HTMLDivElement>(null);
  const map = useRef<maplibregl.Map | null>(null);
  const [task, setTask] = useState<AnnotationTask | null>(null);
  const [labels, setLabels] = useState<GeoJSON.FeatureCollection>({ type: "FeatureCollection", features: [] });
  const [tool, setTool] = useState<Tool>("polygon"); const toolRef = useRef(tool);
  const [cls, setCls] = useState("mining"); const clsRef = useRef(cls);
  const [radius, setRadius] = useState(20); const radiusRef = useRef(radius);
  const [layer, setLayer] = useState("true_colour");
  const [draft, setDraft] = useState<[number, number][]>([]); const draftRef = useRef(draft);
  const [picked, setPicked] = useState<number[]>([]); const pickedRef = useRef(picked);
  const [error, setError] = useState(""); const [busy, setBusy] = useState(false);
  toolRef.current = tool; clsRef.current = cls; radiusRef.current = radius; draftRef.current = draft; pickedRef.current = picked;

  const load = () => api.annotationTask(taskId).then((t) => { setTask(t); setLabels(t.labels); }).catch((e) => setError(e.message));
  useEffect(() => { load(); }, [taskId]);

  const send = async (body: Record<string, unknown>) => {
    setBusy(true); setError("");
    try { setLabels(await api.annotationEdit(taskId, { class: clsRef.current, ...body })); setDraft([]); setPicked([]); }
    catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  };
  const sendRef = useRef(send); sendRef.current = send;

  useEffect(() => {
    if (!mapEl.current) return;
    const m = createMap({ container: mapEl.current, style: BASE_STYLE, center: [0, 0], zoom: 2, doubleClickZoom: false });
    m.addControl(new maplibregl.NavigationControl({ showCompass: false }));
    m.addControl(new maplibregl.ScaleControl({ unit: "metric" }));
    m.on("load", () => {
      markReady(m); m.setLayoutProperty("osm", "visibility", "none");
      m.addSource("labels", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      m.addLayer({ id: "labels-fill", type: "fill", source: "labels", paint: { "fill-color": ["match", ["get", "class"],
        ...Object.entries(CLASS_STYLE).flatMap(([k, v]) => [k, v.colour]), "#888"] as unknown as string, "fill-opacity": 0.25 } });
      for (const [k, v] of Object.entries(CLASS_STYLE))
        m.addLayer({ id: `labels-${k}`, type: "line", source: "labels", filter: ["==", ["get", "class"], k],
          paint: { "line-color": v.colour, "line-width": 2, ...(v.dash ? { "line-dasharray": v.dash } : {}) } });
      m.addLayer({ id: "labels-picked", type: "line", source: "labels", filter: ["==", ["get", "index"], -1], paint: { "line-color": "#fff", "line-width": 4 } });
      m.addSource("draft", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      m.addLayer({ id: "draft", type: "line", source: "draft", paint: { "line-color": "#ffffff", "line-width": 2, "line-dasharray": [2, 1] } });
      m.addLayer({ id: "draft-pts", type: "circle", source: "draft", filter: ["==", ["geometry-type"], "Point"],
        paint: { "circle-radius": 4, "circle-color": "#fff", "circle-stroke-color": "#000", "circle-stroke-width": 1 } });
    });
    const pt = (e: maplibregl.MapMouseEvent): [number, number] => [e.lngLat.lng, e.lngLat.lat];
    let stroke: [number, number][] | null = null;
    m.on("mousedown", (e) => {
      if (toolRef.current !== "brush" && toolRef.current !== "erase") return;
      e.preventDefault(); m.dragPan.disable(); stroke = [pt(e)]; setDraft(stroke);
    });
    m.on("mousemove", (e) => { if (stroke) { stroke = [...stroke, pt(e)]; setDraft(stroke); } });
    m.on("mouseup", () => {
      if (!stroke) return;
      const s = stroke; stroke = null; m.dragPan.enable();
      sendRef.current({ op: toolRef.current, coordinates: s, radius_m: radiusRef.current });
    });
    m.on("click", (e) => {
      const t = toolRef.current;
      if (t === "polygon") setDraft([...draftRef.current, pt(e)]);
      else if (t === "split") {
        const d = [...draftRef.current, pt(e)];
        if (d.length === 2) sendRef.current({ op: "split", coordinates: d }); else setDraft(d);
      } else if (t === "merge" || t === "delete") {
        const f = m.queryRenderedFeatures(e.point, { layers: ["labels-fill"] }).find((x) => x.properties?.class === clsRef.current);
        if (!f) return;
        const i = Number(f.properties!.index);
        if (t === "delete") sendRef.current({ op: "delete", index: i });
        else setPicked(pickedRef.current.includes(i) ? pickedRef.current.filter((x) => x !== i) : [...pickedRef.current, i]);
      }
    });
    m.on("dblclick", (e) => { if (toolRef.current === "polygon" && draftRef.current.length >= 3) { e.preventDefault(); sendRef.current({ op: "polygon", coordinates: draftRef.current }); } });
    map.current = m;
    if (import.meta.env.DEV) (window as unknown as { __annMap: maplibregl.Map }).__annMap = m;   // browser tests only
    return () => m.remove();
  }, []);

  useEffect(() => {   // imagery of the tile
    const m = map.current;
    if (!m || !task) return;
    whenReady(m, async () => {
      try {
        const { bounds } = await api.annotationBounds(taskId, layer);
        const url = api.annotationImageUrl(taskId, layer);
        const src = m.getSource("img") as maplibregl.ImageSource | undefined;
        if (src) src.updateImage({ url, coordinates: corners(bounds) });
        else {
          m.addSource("img", { type: "image", url, coordinates: corners(bounds) });
          m.addLayer({ id: "img", type: "raster", source: "img", paint: { "raster-resampling": "nearest", "raster-fade-duration": 0 } }, "labels-fill");
          m.fitBounds([[bounds[0], bounds[1]], [bounds[2], bounds[3]]], { padding: 20, duration: 0 });
        }
      } catch (e) { setError((e as Error).message); }
    });
  }, [task?.id, layer]);

  useEffect(() => { const m = map.current; if (m) whenReady(m, () => (m.getSource("labels") as maplibregl.GeoJSONSource).setData(labels)); }, [labels]);
  useEffect(() => {
    const m = map.current;
    if (!m) return;
    whenReady(m, () => {
      const pts = draft.map((p) => ({ type: "Feature" as const, properties: {}, geometry: { type: "Point" as const, coordinates: p } }));
      const line = draft.length > 1 ? [{ type: "Feature" as const, properties: {}, geometry: { type: "LineString" as const, coordinates: tool === "polygon" && draft.length > 2 ? [...draft, draft[0]] : draft } }] : [];
      (m.getSource("draft") as maplibregl.GeoJSONSource).setData({ type: "FeatureCollection", features: [...line, ...pts] });
      m.setFilter("labels-picked", ["all", ["==", ["get", "class"], cls], ["in", ["get", "index"], ["literal", picked]]]);
      m.getCanvas().style.cursor = tool === "merge" || tool === "delete" ? "pointer" : "crosshair";
    });
  }, [draft, picked, tool, cls]);

  const counts = labels.features.reduce<Record<string, number>>((c, f) => ({ ...c, [f.properties!.class]: (c[f.properties!.class] ?? 0) + 1 }), {});
  const done = task?.state === "done";
  return (
    <div className="split">
      <aside className="panel">
        <a href="#/pool" className="small">← Annotation</a>
        <h1>Annotate tile {task?.tile}</h1>
        <p className="small muted">Scene {task?.scene} ({task?.date}) · protocol v{task?.protocol_version}. Label what the imagery shows; model candidates are hidden.</p>
        <label className="small">Imagery<select value={layer} onChange={(e) => setLayer(e.target.value)}>
          <option value="true_colour">True colour</option><option value="swir_false_colour">SWIR false colour</option>
          <option value="nir_false_colour">NIR false colour</option><option value="ndvi">NDVI</option><option value="quality">Quality mask</option></select></label>
        <label className="small">Class<select value={cls} onChange={(e) => { setCls(e.target.value); setPicked([]); }}>
          {(task?.classes ?? Object.keys(CLASS_STYLE)).map((c) => <option key={c} value={c}>{c === "mining" ? "Mining disturbance" : `Hard negative: ${human(c)}`}</option>)}</select></label>
        <div className="actions wrap">{TOOLS.map(([k, v]) => <button key={k} className={tool === k ? "primary" : ""} disabled={done}
          onClick={() => { setTool(k); setDraft([]); setPicked([]); }}>{v}</button>)}
          <button disabled={busy || done} onClick={() => send({ op: "undo" })}>Undo</button></div>
        <p className="small muted">{{ polygon: "Click the corners; double-click or Finish to close.", brush: "Drag to paint.", erase: "Drag to erase.",
          split: "Click two points on a line across a polygon.", merge: "Click touching polygons of this class, then Merge.", delete: "Click a polygon of this class to delete it." }[tool]}</p>
        {(tool === "brush" || tool === "erase") && <label className="small">Brush radius: {radius} m
          <input type="range" min={5} max={150} step={5} value={radius} onChange={(e) => setRadius(Number(e.target.value))} /></label>}
        {tool === "polygon" && draft.length >= 3 && <button className="primary" disabled={busy} onClick={() => send({ op: "polygon", coordinates: draft })}>Finish polygon</button>}
        {tool === "merge" && picked.length >= 2 && <button className="primary" disabled={busy} onClick={() => send({ op: "merge", indices: picked })}>Merge {picked.length}</button>}
        {draft.length > 0 && <button onClick={() => setDraft([])}>Clear draft</button>}
        {error && <p className="error small" role="alert">{error}</p>}
        <ul className="plain small legend-list">{Object.entries(CLASS_STYLE).filter(([k]) => counts[k]).map(([k, v]) => <li key={k}>
          <svg width="22" height="8"><line x1="0" y1="4" x2="22" y2="4" stroke={v.colour} strokeWidth="2" strokeDasharray={v.dash?.map((d) => d * 2).join(" ")} /></svg>
          {" "}{human(k)}: {counts[k]}</li>)}</ul>
        <div className="actions wrap">
          <a className="button" href={`/api/annotation-tasks/${taskId}/mask/`}>Export mask (.zip)</a>
          <button onClick={() => api.annotationState(taskId, done ? "open" : "done").then(setTask).catch((e) => setError(e.message))}>{done ? "Reopen" : "Mark done"}</button>
        </div>
      </aside>
      <div className="err-map"><div ref={mapEl} className="map" /></div>
    </div>
  );
}
