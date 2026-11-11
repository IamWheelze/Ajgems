import maplibregl from "maplibre-gl";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, Area, DetectionFeature, Me, MissedProps, PixelInfo, Project, ReviewOptions } from "./api";
import { BASE_STYLE, markReady, whenReady } from "./mapStyle";
import SwipeCompare from "./SwipeCompare";
import { ACTION_LABEL, BAND_LABEL, human, SHORTCUTS, STATUS } from "./status";

const LAYERS: Record<string, string> = {
  true_colour: "True colour (B4 B3 B2)", swir_false_colour: "SWIR false colour (B12 B8A B4)",
  nir_false_colour: "NIR false colour (B8 B4 B3)", ndvi: "NDVI", ndwi: "NDWI", quality: "Quality mask (cloud, shadow, no data)",
};
// FR-122: fixed, documented stretches (same text as minefoot.viewer.LAYER_TEXT), so a scene looks the same in every session.
const STRETCH_NOTE: Record<string, string> = {
  true_colour: "Fixed stretch: reflectance 0 to 0.35.", swir_false_colour: "Fixed stretch: reflectance 0 to 0.50. Bare ground stands out from vegetation.",
  nir_false_colour: "Fixed stretch: reflectance 0 to 0.50. Vegetation looks red.", ndvi: "Dark is low vegetation, light is high. Fixed scale −0.2 to 0.8.",
  ndwi: "Light is water-like. Fixed scale −0.6 to 0.6.", quality: "Scene classification (SCL) of unusable pixels over a dimmed true-colour view.",
};
// FR-123: the quality layer's classes (minefoot.viewer.SCL_COLOURS).
const QUALITY_KEY: [string, string][] = [["rgb(0,0,0)", "No data"], ["rgb(120,0,120)", "Saturated or defective"], ["rgb(70,70,110)", "Cloud shadow"],
  ["rgb(200,200,200)", "Cloud, medium"], ["rgb(255,255,255)", "Cloud, high"], ["rgb(150,210,240)", "Thin cirrus"], ["rgb(255,150,0)", "Snow or ice (often bright mine ground)"]];
const BAND_ORDER: Record<string, number> = { high: 0, medium: 1, low: 2 };
const NO_FILTERS = { score_band: "", min_ha: "", max_ha: "", date_from: "", date_to: "", model_version: "", reason: "", area: "" };
// FR-093: analyst-added missed sites, drawn white over a black casing so they never look like a candidate status.
const MISSED = { colour: "#FFFFFF", casing: "#000000", icon: "＋", label: "Missed site (added by analyst)" };

export default function ReviewWorkspace({ projectId, me }: { projectId: number; me: Me }) {
  const mapEl = useRef<HTMLDivElement>(null);
  const map = useRef<maplibregl.Map | null>(null);
  const [project, setProject] = useState<Project | null>(null);
  const [features, setFeatures] = useState<DetectionFeature[]>([]);
  const [options, setOptions] = useState<ReviewOptions>({});
  const [filter, setFilter] = useState("unreviewed");
  const [selected, setSelected] = useState<DetectionFeature | null>(null);
  const [layer, setLayer] = useState("true_colour");
  const [compare, setCompare] = useState("");   // FR-127 swipe: the right-hand preset, "" when off
  const [mapObj, setMapObj] = useState<maplibregl.Map | null>(null);
  const [showOsm, setShowOsm] = useState(false);
  const [showNA, setShowNA] = useState(true);
  const [showScore, setShowScore] = useState(false);   // FR-124 heatmap
  const showScoreRef = useRef(showScore);
  const [scoreMissing, setScoreMissing] = useState(false);
  const [inspecting, setInspecting] = useState(false);   // FR-128
  const inspectRef = useRef<((lng: number, lat: number) => void) | null>(null);
  const [pixel, setPixel] = useState<PixelInfo | null>(null);
  const [pixelError, setPixelError] = useState("");
  const [cursor, setCursor] = useState<[number, number] | null>(null);   // FR-120 coordinates
  const [editRing, setEditRing] = useState<[number, number][] | null>(null);
  const editRef = useRef<[number, number][] | null>(null);
  const [imagery, setImagery] = useState<{ date: string; scene_id: string; licence_note: string } | null>(null);
  const [action, setAction] = useState("");
  const [reason, setReason] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [filters, setFilters] = useState(NO_FILTERS);   // FR-094, applied on the server
  const [draftFilters, setDraftFilters] = useState(NO_FILTERS);
  const [areas, setAreas] = useState<Area[]>([]);
  const [missed, setMissed] = useState<GeoJSON.Feature<GeoJSON.Polygon, MissedProps>[]>([]);
  const [missedReasons, setMissedReasons] = useState<string[]>([]);
  const [missedPts, setMissedPts] = useState<[number, number][] | null>(null);   // null: not drawing
  const missedRef = useRef<[number, number][] | null>(null);
  const [missedReason, setMissedReason] = useState("");
  const [missedNote, setMissedNote] = useState("");

  useEffect(() => {
    Promise.all([api.project(projectId), api.reviewOptions(), api.areas(projectId)])
      .then(([p, o, a]) => { setProject(p); setOptions(o); setAreas(a); })
      .catch((e) => setError(e.message));
    if (me.roles.some((r) => ["admin", "gis", "field"].includes(r)))
      api.missedSites(projectId).then((fc) => { setMissed(fc.features); setMissedReasons(fc.reasons); }).catch((e) => setError(e.message));
  }, [projectId, me.roles]);
  useEffect(() => {
    api.detections(projectId, filters).then((fc) => { setFeatures(fc.features as DetectionFeature[]); setSelected(null); })
      .catch((e) => setError(e.message));
  }, [projectId, filters]);
  const activeFilters = Object.values(filters).filter(Boolean).length;

  const queue = useMemo(() => features
    .filter((f) => filter === "all" || f.properties.status === filter)
    .sort((a, b) => BAND_ORDER[a.properties.score_band] - BAND_ORDER[b.properties.score_band] || b.properties.area_ha - a.properties.area_ha),
  [features, filter]);
  const reviewed = features.filter((f) => f.properties.status !== "unreviewed").length;
  const counts = useMemo(() => features.reduce<Record<string, number>>((c, f) => ({ ...c, [f.properties.status]: (c[f.properties.status] ?? 0) + 1 }), {}), [features]);

  const select = useCallback((f: DetectionFeature | null) => {
    setAction(""); setReason(""); setNote(""); setError(""); setEditRing(null);
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
      m.addSource("missed", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      m.addLayer({ id: "missed-casing", type: "line", source: "missed", paint: { "line-color": MISSED.casing, "line-width": 4 } });
      m.addLayer({ id: "missed-line", type: "line", source: "missed", paint: { "line-color": MISSED.colour, "line-width": 2, "line-dasharray": [3, 1] } });
      m.addSource("missed-draft", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      m.addLayer({ id: "missed-draft-line", type: "line", source: "missed-draft", filter: ["==", ["geometry-type"], "LineString"],
        paint: { "line-color": MISSED.colour, "line-width": 2, "line-dasharray": [3, 1] } });
      m.addLayer({ id: "missed-draft-pts", type: "circle", source: "missed-draft", filter: ["==", ["geometry-type"], "Point"],
        paint: { "circle-radius": 4, "circle-color": MISSED.colour, "circle-stroke-color": MISSED.casing, "circle-stroke-width": 2 } });
      m.on("mousemove", (e) => setCursor([e.lngLat.lng, e.lngLat.lat]));
      m.on("mouseout", () => setCursor(null));
      m.on("click", (e) => { if (inspectRef.current) inspectRef.current(e.lngLat.lng, e.lngLat.lat); });
      m.on("click", (e) => { if (missedRef.current) setMissedPts([...missedRef.current, [e.lngLat.lng, e.lngLat.lat]]); });
      m.addSource("edit", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      m.addLayer({ id: "edit-line", type: "line", source: "edit", filter: ["==", ["geometry-type"], "Polygon"],
        paint: { "line-color": "#56B4E9", "line-width": 2.5 } });
      m.addLayer({ id: "edit-pts", type: "circle", source: "edit", filter: ["==", ["geometry-type"], "Point"],
        paint: { "circle-radius": 5, "circle-color": "#ffffff", "circle-stroke-color": "#0072B2", "circle-stroke-width": 2 } });
      let drag: number | null = null;
      m.on("mousedown", "edit-pts", (e) => {
        e.preventDefault(); drag = e.features?.[0]?.properties?.i ?? null; m.dragPan.disable();
      });
      m.on("mousemove", (e) => {
        if (drag === null || !editRef.current) return;
        const next = editRef.current.map((p, i) => (i === drag ? [e.lngLat.lng, e.lngLat.lat] as [number, number] : p));
        setEditRing(next);
      });
      m.on("mouseup", () => { if (drag !== null) { drag = null; m.dragPan.enable(); } });
      m.on("click", "dets-fill", (e) => {
        if (missedRef.current || inspectRef.current) return;   // drawing a missed site, or inspecting pixels
        const id = e.features?.[0]?.properties?.id;
        const f = featuresRef.current.find((x) => x.properties.id === id);
        if (f) select(f);
      });
      m.on("mouseenter", "dets-fill", () => (m.getCanvas().style.cursor = "pointer"));
      m.on("mouseleave", "dets-fill", () => (m.getCanvas().style.cursor = ""));
    });
    map.current = m;
    setMapObj(m);
    if (import.meta.env.DEV) (window as unknown as { __reviewMap: maplibregl.Map }).__reviewMap = m;   // browser tests only
    return () => m.remove();
  }, [select]);

  const dets = useMemo<GeoJSON.FeatureCollection>(() => ({ type: "FeatureCollection",
    features: features.map((f) => ({ ...f, properties: { id: f.properties.id, status: f.properties.status } })) as GeoJSON.Feature[] }), [features]);
  const featuresRef = useRef(features);
  useEffect(() => {
    featuresRef.current = features;
    const m = map.current;
    const apply = () => (m!.getSource("dets") as maplibregl.GeoJSONSource)?.setData(dets);
    if (m) whenReady(m, apply);
  }, [features, dets]);

  useEffect(() => {
    const m = map.current;
    if (m) whenReady(m, () => (m.getSource("missed") as maplibregl.GeoJSONSource).setData({ type: "FeatureCollection", features: missed }));
  }, [missed]);
  useEffect(() => {
    missedRef.current = missedPts;
    const m = map.current;
    if (!m) return;
    m.getCanvas().style.cursor = missedPts ? "crosshair" : "";
    const pts = missedPts ?? [];
    const ring = pts.length > 2 ? [...pts, pts[0]] : pts;
    whenReady(m, () => (m.getSource("missed-draft") as maplibregl.GeoJSONSource).setData({ type: "FeatureCollection", features: [
      ...(ring.length > 1 ? [{ type: "Feature" as const, properties: {}, geometry: { type: "LineString" as const, coordinates: ring } }] : []),
      ...pts.map((p) => ({ type: "Feature" as const, properties: {}, geometry: { type: "Point" as const, coordinates: p } })),
    ] }));
  }, [missedPts]);
  const saveMissed = async () => {
    if (!missedPts || missedPts.length < 3) return;
    setBusy(true); setError("");
    try {
      const f = await api.addMissedSite(projectId, { type: "Polygon", coordinates: [[...missedPts, missedPts[0]]] }, missedReason, missedNote);
      setMissed((ms) => [...ms, f]); setMissedPts(null); setMissedReason(""); setMissedNote("");
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  };

  useEffect(() => {
    const m = map.current;
    if (m && m.getLayer("osm")) m.setLayoutProperty("osm", "visibility", showOsm ? "visible" : "none");
  }, [showOsm]);

  useEffect(() => {
    showScoreRef.current = showScore;
    const m = map.current;
    if (m && m.getLayer("score")) m.setLayoutProperty("score", "visibility", showScore && !scoreMissing ? "visible" : "none");
  }, [showScore, scoreMissing]);

  useEffect(() => {
    const m = map.current;
    inspectRef.current = inspecting && selected ? (lng, lat) => {
      setPixelError("");
      api.pixel(selected.properties.id, lng, lat).then(setPixel).catch((e) => { setPixel(null); setPixelError(e.message); });
      const src = m?.getSource("pixel") as maplibregl.GeoJSONSource | undefined;
      src?.setData({ type: "Point", coordinates: [lng, lat] });
    } : null;
    if (m) {
      m.getCanvas().style.cursor = inspecting ? "crosshair" : "";
      whenReady(m, () => {
        if (!m.getSource("pixel")) {
          m.addSource("pixel", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
          m.addLayer({ id: "pixel", type: "circle", source: "pixel", paint: { "circle-radius": 5, "circle-color": "rgba(0,0,0,0)",
            "circle-stroke-color": "#56B4E9", "circle-stroke-width": 2 } });
        }
        m.setLayoutProperty("pixel", "visibility", inspecting ? "visible" : "none");
      });
    }
    if (!inspecting) { setPixel(null); setPixelError(""); }
  }, [inspecting, selected]);

  const showNARef = useRef(showNA);
  useEffect(() => {
    showNARef.current = showNA;
    const m = map.current;
    if (m && m.getLayer("na")) m.setLayoutProperty("na", "visibility", showNA ? "visible" : "none");
  }, [showNA]);

  // Edit mode: start from a simplified outline; show draggable vertices.
  useEffect(() => {
    if (action === "edit" && selected && !editRing)   // a valid outline of at most 200 vertices, simplified on the server
      api.editGeometry(selected.properties.id).then((g) => setEditRing((g.coordinates[0] as [number, number][]).slice(0, -1)))
        .catch((e) => setError(e.message));
    if (action !== "edit" && editRing) setEditRing(null);
  }, [action, selected]);
  useEffect(() => {
    editRef.current = editRing;
    const src = map.current?.getSource("edit") as maplibregl.GeoJSONSource | undefined;
    if (!src) return;
    src.setData({ type: "FeatureCollection", features: editRing ? [
      { type: "Feature", properties: {}, geometry: { type: "Polygon", coordinates: [[...editRing, editRing[0]]] } },
      ...editRing.map((p, i) => ({ type: "Feature" as const, properties: { i }, geometry: { type: "Point" as const, coordinates: p } })),
    ] : [] });
  }, [editRing]);

  // Show the selected candidate's own scene, dated, under the polygons.
  useEffect(() => {
    const m = map.current;
    if (!m || !selected) return;
    const detId = selected.properties.id;
    const run = async () => {
      m.setFilter("dets-selected", ["==", ["get", "id"], selected.properties.id]);
      const coords = selected.geometry.coordinates[0] as [number, number][];
      const b = coords.reduce((bb, c) => bb.extend(c), new maplibregl.LngLatBounds(coords[0], coords[0]));
      m.fitBounds(b, { padding: 120, maxZoom: 15, duration: 300 });
      try {
        const meta = await api.detectionBounds(detId, layer);
        setImagery(meta);
        const [w, s, e, n] = meta.bounds;
        const corners: [[number, number], [number, number], [number, number], [number, number]] = [[w, n], [e, n], [e, s], [w, s]];
        const url = api.detectionImageUrl(detId, layer);
        const src = m.getSource("scene") as maplibregl.ImageSource | undefined;
        if (src) src.updateImage({ url, coordinates: corners });
        else {
          m.addSource("scene", { type: "image", url, coordinates: corners });
          m.addLayer({ id: "scene", type: "raster", source: "scene", paint: { "raster-resampling": "nearest", "raster-fade-duration": 0 } }, "dets-fill");
        }
        // FR-125: pixels the quality mask removed are hatched, so unassessed ground never looks clean.
        const naMeta = await api.detectionBounds(detId, "not_assessed");
        const [nw, ns, ne, nn] = naMeta.bounds;
        const naCorners: typeof corners = [[nw, nn], [ne, nn], [ne, ns], [nw, ns]];
        const naUrl = api.detectionImageUrl(detId, "not_assessed");
        const na = m.getSource("na") as maplibregl.ImageSource | undefined;
        if (na) na.updateImage({ url: naUrl, coordinates: naCorners });
        else {
          m.addSource("na", { type: "image", url: naUrl, coordinates: naCorners });
          m.addLayer({ id: "na", type: "raster", source: "na", paint: { "raster-resampling": "nearest", "raster-fade-duration": 0 } }, "dets-fill");
        }
        m.setLayoutProperty("na", "visibility", showNARef.current ? "visible" : "none");
        // FR-124: the job's model-score map as a heatmap, between the imagery and the hatch.
        try {
          const sm = await api.detectionBounds(detId, "score");
          const [sw, ss, se, sn] = sm.bounds;
          const sCorners: typeof corners = [[sw, sn], [se, sn], [se, ss], [sw, ss]];
          const sUrl = api.detectionImageUrl(detId, "score");
          const sc = m.getSource("score") as maplibregl.ImageSource | undefined;
          if (sc) sc.updateImage({ url: sUrl, coordinates: sCorners });
          else {
            m.addSource("score", { type: "image", url: sUrl, coordinates: sCorners });
            m.addLayer({ id: "score", type: "raster", source: "score", paint: { "raster-resampling": "nearest", "raster-fade-duration": 0 } }, "na");
          }
          m.setLayoutProperty("score", "visibility", showScoreRef.current ? "visible" : "none");
          setScoreMissing(false);
        } catch {
          if (m.getLayer("score")) m.setLayoutProperty("score", "visibility", "none");
          setScoreMissing(true);
        }
      } catch (e) { setError((e as Error).message); }
    };
    whenReady(m, run);
  }, [selected?.properties.id, layer]);

  const allowed = useMemo(() => {
    if (!selected) return [];
    return Object.entries(options).filter(([, o]) => o.roles.some((r) => me.roles.includes(r))
      && (!o.allowed_from || o.allowed_from.includes(selected.properties.status))).map(([a]) => a);
  }, [options, selected, me.roles]);

  const submit = useCallback(async () => {
    if (!selected || !action || !reason) return;
    setBusy(true); setError("");
    try {
      const geometry = action === "edit" && editRing ? { type: "Polygon" as const, coordinates: [[...editRing, editRing[0]]] } : undefined;
      const updated = await api.review(selected.properties.id, action, reason, note, geometry);
      setFeatures((fs) => fs.map((f) => (f.properties.id === updated.properties.id ? updated : f)));
      const idx = queue.findIndex((f) => f.properties.id === selected.properties.id);
      const next = queue.filter((f) => f.properties.id !== selected.properties.id)[Math.max(0, idx)] ?? null;
      if (filter === "all" || updated.properties.status === filter) setSelected(updated); else select(next);
      setAction(""); setReason(""); setNote("");
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }, [selected, action, reason, note, queue, filter, select, editRing]);

  // Keyboard review: c/r/u/f choose an action, j/k move through the queue, Enter submits.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement).closest("input, textarea, select") || missedRef.current) return;
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
        <span className="small muted">{reviewed} of {features.length} reviewed{activeFilters ? " (filtered)" : ""} · {missed.length} missed site{missed.length === 1 ? "" : "s"} added</span>
      </div>

      <aside className="ws-queue">
        <label className="small">Show
          <select value={filter} onChange={(e) => { setFilter(e.target.value); setSelected(null); }}>
            <option value="all">All ({features.length})</option>
            {Object.entries(STATUS).map(([s, x]) => <option key={s} value={s}>{x.icon} {x.label} ({counts[s] ?? 0})</option>)}
          </select>
        </label>
        <details className="filters-more small" open={activeFilters > 0}>
          <summary>More filters{activeFilters ? ` (${activeFilters})` : ""}</summary>
          <form className="filter-grid" onSubmit={(e) => { e.preventDefault(); setFilters(draftFilters); }}>
            <label>Score band<select value={draftFilters.score_band} onChange={(e) => setDraftFilters({ ...draftFilters, score_band: e.target.value })}>
              <option value="">Any</option>{Object.entries(BAND_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>
            <label>Area of interest<select value={draftFilters.area} onChange={(e) => setDraftFilters({ ...draftFilters, area: e.target.value })}>
              <option value="">Any</option>{areas.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}</select></label>
            <label>Min ha<input type="number" min="0" step="any" value={draftFilters.min_ha} onChange={(e) => setDraftFilters({ ...draftFilters, min_ha: e.target.value })} /></label>
            <label>Max ha<input type="number" min="0" step="any" value={draftFilters.max_ha} onChange={(e) => setDraftFilters({ ...draftFilters, max_ha: e.target.value })} /></label>
            <label>Image from<input type="date" value={draftFilters.date_from} onChange={(e) => setDraftFilters({ ...draftFilters, date_from: e.target.value })} /></label>
            <label>Image to<input type="date" value={draftFilters.date_to} onChange={(e) => setDraftFilters({ ...draftFilters, date_to: e.target.value })} /></label>
            <label>Model<input value={draftFilters.model_version} placeholder="e.g. g2_hn:unet" onChange={(e) => setDraftFilters({ ...draftFilters, model_version: e.target.value })} /></label>
            <label>Last reason<select value={draftFilters.reason} onChange={(e) => setDraftFilters({ ...draftFilters, reason: e.target.value })}>
              <option value="">Any</option>{[...new Set(Object.values(options).flatMap((o) => o.reasons))].sort().map((r) => <option key={r} value={r}>{human(r)}</option>)}</select></label>
            <div className="actions"><button className="primary">Apply</button>
              <button type="button" onClick={() => { setDraftFilters(NO_FILTERS); setFilters(NO_FILTERS); }}>Clear</button></div>
          </form>
        </details>
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
        {mapObj && compare && compare !== layer && selected && (
          <SwipeCompare main={mapObj} detectionId={selected.properties.id} layer={compare}
            label={`◀ ${LAYERS[layer]}  |  ${LAYERS[compare]} ▶`} outline={selected as unknown as GeoJSON.Feature} all={dets} showNA={showNA} />
        )}
        <details className="map-overlay" open>
          <summary className="small"><b>Layers</b> · {LAYERS[layer]}</summary>
          <select value={layer} onChange={(e) => setLayer(e.target.value)} aria-label="Band preset">
            {Object.entries(LAYERS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
          <div className="small muted stretch-note">{STRETCH_NOTE[layer]}</div>
          {layer === "quality" && <div className="quality-key small">{QUALITY_KEY.map(([c, l]) => <span key={l}><span className="swatch" style={{ background: c }} /> {l}</span>)}</div>}
          <label className="small"><input type="checkbox" checked={showScore} onChange={(e) => setShowScore(e.target.checked)} disabled={scoreMissing} />
            {" "}Model-score heatmap{scoreMissing ? " (not stored for this candidate)" : ""}</label>
          {showScore && !scoreMissing && <div className="score-key small"><span>0.1</span><span className="score-ramp" /><span>1.0</span>
            <span className="muted">model score, not a probability; threshold {p ? p.threshold.toFixed(2) : "—"}</span></div>}
          <label className="small"><input type="checkbox" checked={inspecting} onChange={(e) => { setInspecting(e.target.checked); if (e.target.checked) setMissedPts(null); }} />
            {" "}Pixel inspector (click the map)</label>
          <label className="small">Swipe compare
            <select value={compare} onChange={(e) => setCompare(e.target.value)} aria-label="Swipe comparison preset">
              <option value="">Off</option>
              {Object.entries(LAYERS).filter(([k]) => k !== layer).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
          </label>
          <label className="small"><input type="checkbox" checked={showNA} onChange={(e) => setShowNA(e.target.checked)} /> Not assessed (hatched)</label>
          <label className="small"><input type="checkbox" checked={showOsm} onChange={(e) => setShowOsm(e.target.checked)} /> OpenStreetMap (orientation only)</label>
          {imagery && <div className="small">{imagery.scene_id}<br />{imagery.licence_note}</div>}
        </details>
        {inspecting && (pixel || pixelError) && (
          <div className="pixel-panel small" role="status">
            <div className="row gap"><b className="grow">Pixel</b><button className="link" onClick={() => { setPixel(null); setPixelError(""); }} aria-label="Close">✕</button></div>
            {pixelError ? <p className="error">{pixelError}</p> : pixel && (
              <>
                <div className="muted">{pixel.lat.toFixed(5)}°, {pixel.lon.toFixed(5)}° · tile {pixel.tile}, row {pixel.row}, col {pixel.col}</div>
                <table className="table"><tbody>
                  {Object.entries(pixel.reflectance).map(([b, v]) => <tr key={b}><td>{b}</td><td>{v === null ? "no data" : v.toFixed(4)}</td></tr>)}
                  <tr><td>NDVI</td><td>{pixel.ndvi?.toFixed(3) ?? "—"}</td></tr>
                  <tr><td>NDWI</td><td>{pixel.ndwi?.toFixed(3) ?? "—"}</td></tr>
                  <tr><td>Mask</td><td>{pixel.scl_class} (SCL {pixel.scl}) · {pixel.usable ? "usable" : <b>not assessed</b>}{pixel.suspect_bright && " · bright, check"}</td></tr>
                  <tr><td>Model score</td><td>{pixel.model_score === null ? "not stored" : `${pixel.model_score.toFixed(3)} (threshold ${pixel.threshold.toFixed(2)})`}</td></tr>
                  <tr><td>Scene</td><td>{pixel.scene ? `${pixel.scene.id}, ${pixel.scene.date}, baseline ${pixel.scene.processing_baseline}` : "—"}</td></tr>
                </tbody></table>
                <div className="muted">Reflectance from the job tile, unresampled ({pixel.crs}). Model score, not a probability.</div>
              </>
            )}
          </div>
        )}
        {cursor && <div className="coords small" aria-live="off">{cursor[1].toFixed(5)}°, {cursor[0].toFixed(5)}° (WGS 84)</div>}
        <div className="legend small">
          {Object.entries(STATUS).map(([s, x]) => (
            <span key={s}><svg width="22" height="8"><line x1="0" y1="4" x2="22" y2="4" stroke={x.colour} strokeWidth="2"
              strokeDasharray={x.dash ? x.dash.map((d) => d * 2).join(" ") : undefined} /></svg> {x.icon} {x.label}</span>
          ))}
          {missed.length > 0 && <span><svg width="22" height="8"><line x1="0" y1="4" x2="22" y2="4" stroke={MISSED.casing} strokeWidth="4" />
            <line x1="0" y1="4" x2="22" y2="4" stroke={MISSED.colour} strokeWidth="2" strokeDasharray="6 2" /></svg> {MISSED.icon} {MISSED.label}</span>}
        </div>
      </div>

      <aside className="ws-evidence">
        {me.roles.includes("gis") && (
          <div className="card missed">
            {missedPts === null ? (
              <button onClick={() => { setMissedPts([]); setAction(""); setInspecting(false); }} title="FR-093: record disturbance the model did not find">{MISSED.icon} Add missed site</button>
            ) : (
              <>
                <h3>{MISSED.icon} Missed site</h3>
                <p className="small muted">Click the map to outline disturbance the model did not find ({missedPts.length} points). It is counted as a false negative, separately from candidates, unless that tile had no usable imagery.</p>
                <label className="small">Reason (required)
                  <select value={missedReason} onChange={(e) => setMissedReason(e.target.value)}>
                    <option value="">Choose…</option>{missedReasons.map((r) => <option key={r} value={r}>{human(r)}</option>)}</select></label>
                <label className="small">Note{missedReason === "other" && " (required)"}<textarea rows={2} value={missedNote} onChange={(e) => setMissedNote(e.target.value)} /></label>
                <div className="actions wrap">
                  <button className="primary" disabled={busy || missedPts.length < 3 || !missedReason || (missedReason === "other" && !missedNote.trim())} onClick={saveMissed}>Save missed site</button>
                  <button onClick={() => setMissedPts(missedPts.slice(0, -1))} disabled={!missedPts.length}>Undo point</button>
                  <button onClick={() => setMissedPts(null)}>Cancel</button>
                </div>
              </>
            )}
          </div>
        )}
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
                {action === "edit" && <p className="small muted">Drag the white vertices on the map. The original outline is kept in the history.</p>}
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
