import maplibregl from "maplibre-gl";
import { useEffect, useRef, useState } from "react";
import { api, Area, Me, Project } from "./api";
import { BASE_STYLE, markReady, whenReady } from "./mapStyle";

// Areas of interest (FR-011, FR-012): draw by clicking, or upload a file. The server validates
// geometry, CRS and the size limit, and computes the geodesic area.
export default function ProjectAreas({ projectId, me }: { projectId: number; me: Me }) {
  const mapEl = useRef<HTMLDivElement>(null);
  const map = useRef<maplibregl.Map | null>(null);
  const [project, setProject] = useState<Project | null>(null);
  const [areas, setAreas] = useState<Area[]>([]);
  const [drawing, setDrawing] = useState(false);
  const [points, setPoints] = useState<[number, number][]>([]);
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  const canEdit = me.roles.includes("gis") && project?.status === "active";

  const load = () => Promise.all([api.project(projectId), api.areas(projectId)])
    .then(([p, a]) => { setProject(p); setAreas(a); }).catch((e) => setError(e.message));
  useEffect(() => { load(); }, [projectId]);

  useEffect(() => {
    if (!mapEl.current) return;
    const m = new maplibregl.Map({ container: mapEl.current, style: BASE_STYLE, center: [8.0, 9.0], zoom: 5 });
    m.addControl(new maplibregl.NavigationControl({ showCompass: false }));
    m.addControl(new maplibregl.ScaleControl({ unit: "metric" }));
    m.on("load", () => {
      markReady(m);
      m.addSource("areas", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      m.addLayer({ id: "areas-fill", type: "fill", source: "areas", paint: { "fill-color": "#0072B2", "fill-opacity": 0.15 } });
      m.addLayer({ id: "areas-line", type: "line", source: "areas", paint: { "line-color": "#0072B2", "line-width": 2 } });
      m.addSource("draft", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      m.addLayer({ id: "draft-line", type: "line", source: "draft", paint: { "line-color": "#D55E00", "line-width": 2, "line-dasharray": [2, 1] } });
      m.addLayer({ id: "draft-pts", type: "circle", source: "draft", paint: { "circle-radius": 4, "circle-color": "#D55E00" } });
    });
    map.current = m;
    return () => m.remove();
  }, []);

  // Show saved areas and fit the map to them.
  useEffect(() => {
    const m = map.current;
    if (!m) return;
    const apply = () => {
      const src = m.getSource("areas") as maplibregl.GeoJSONSource | undefined;
      if (!src) return;
      src.setData({ type: "FeatureCollection", features: areas.map((a) => ({ type: "Feature", properties: { name: a.name }, geometry: a.geometry })) });
      if (areas.length) {
        const b = new maplibregl.LngLatBounds();
        areas.forEach((a) => (a.geometry as GeoJSON.MultiPolygon).coordinates.flat(2).forEach((c) => b.extend(c as [number, number])));
        m.fitBounds(b, { padding: 40, maxZoom: 12 });
      }
    };
    whenReady(m, apply);
  }, [areas]);

  // Click to add vertices while drawing.
  useEffect(() => {
    const m = map.current;
    if (!m) return;
    const onClick = (e: maplibregl.MapMouseEvent) => drawing && setPoints((p) => [...p, [e.lngLat.lng, e.lngLat.lat]]);
    m.on("click", onClick);
    m.getCanvas().style.cursor = drawing ? "crosshair" : "";
    return () => { m.off("click", onClick); };
  }, [drawing]);

  useEffect(() => {
    const src = map.current?.getSource("draft") as maplibregl.GeoJSONSource | undefined;
    if (!src) return;
    const ring = points.length > 2 ? [...points, points[0]] : points;
    src.setData({ type: "FeatureCollection", features: [
      ...(ring.length > 1 ? [{ type: "Feature" as const, properties: {}, geometry: { type: "LineString" as const, coordinates: ring } }] : []),
      ...points.map((p) => ({ type: "Feature" as const, properties: {}, geometry: { type: "Point" as const, coordinates: p } })),
    ] });
  }, [points]);

  const save = async () => {
    setError("");
    try {
      await api.addArea(projectId, name || `Area ${areas.length + 1}`, { type: "Polygon", coordinates: [[...points, points[0]]] });
      setPoints([]); setDrawing(false); setName(""); load();
    } catch (e) { setError((e as Error).message); }
  };

  const upload = async (file: File) => {
    setError("");
    try { await api.uploadArea(projectId, name || file.name, file); setName(""); load(); }
    catch (e) { setError((e as Error).message); }
  };

  return (
    <div className="split">
      <aside className="panel">
        <a href="#/projects" className="small">← Projects</a>
        <h1>{project?.name ?? "…"}</h1>
        <p className="muted small">{project?.purpose}</p>
        <a className="button primary" href={`#/projects/${projectId}/review`}>Open review workspace</a>
        <h2>Areas of interest</h2>
        {areas.length === 0 && <p className="muted">No areas yet.</p>}
        <ul className="plain">
          {areas.map((a) => <li key={a.id}><b>{a.name}</b> · {a.area_km2.toLocaleString(undefined, { maximumFractionDigits: 1 })} km² · {a.source} ({a.source_crs})</li>)}
        </ul>
        {canEdit && (
          <div className="card">
            <h3>Add an area</h3>
            <label>Name<input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Jos Plateau pilot" /></label>
            {!drawing ? (
              <button onClick={() => { setDrawing(true); setPoints([]); }}>Draw on the map</button>
            ) : (
              <div className="row gap">
                <span className="small muted">{points.length} points — click the map to add</span>
                <button className="primary" disabled={points.length < 3} onClick={save}>Save area</button>
                <button onClick={() => { setDrawing(false); setPoints([]); }}>Cancel</button>
              </div>
            )}
            <label className="small">…or upload GeoJSON, KML, zipped Shapefile or GeoPackage
              <input type="file" accept=".geojson,.json,.kml,.zip,.gpkg" onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} />
            </label>
            <p className="small muted">Limit 10,000 km² per job. Area is computed on the ellipsoid.</p>
          </div>
        )}
        {error && <p className="error" role="alert">{error}</p>}
      </aside>
      <div ref={mapEl} className="map" />
    </div>
  );
}
