import maplibregl from "maplibre-gl";
import { useEffect, useRef, useState } from "react";
import { api, DatasetInfo, SampleDetail } from "./api";
import { BASE_STYLE, markReady, whenReady } from "./mapStyle";
import { human } from "./status";

// Dataset explorer (FR-131): sites by split, filters, class balance, labels, overlap warnings.
const SPLIT_COLOUR: Record<string, string> = { train: "#0072B2", val: "#E69F00", test: "#CC79A7", held_out_near_locked_test: "#999999" };
const FILTERS = ["split", "mine_scale", "mine_type", "region"] as const;

export default function DatasetExplorer() {
  const mapEl = useRef<HTMLDivElement>(null);
  const map = useRef<maplibregl.Map | null>(null);
  const [datasets, setDatasets] = useState<DatasetInfo[]>([]);
  const [ds, setDs] = useState<number | null>(null);
  const [filters, setFilters] = useState<Record<string, string>>({});
  const [data, setData] = useState<Awaited<ReturnType<typeof api.samples>> | null>(null);
  const [picked, setPicked] = useState<SampleDetail | null>(null);
  const [error, setError] = useState("");

  useEffect(() => { api.datasets().then((d) => { setDatasets(d); if (d.length) setDs(d[0].id); }).catch((e) => setError(e.message)); }, []);
  useEffect(() => {
    if (ds === null) return;
    const q = Object.entries(filters).filter(([, v]) => v).map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join("&");
    api.samples(ds, q ? `?${q}` : "").then(setData).catch((e) => setError(e.message));
  }, [ds, filters]);

  useEffect(() => {
    if (!mapEl.current) return;
    const m = new maplibregl.Map({ container: mapEl.current, style: BASE_STYLE, center: [10, 5], zoom: 1.3 });
    m.addControl(new maplibregl.NavigationControl({ showCompass: false }));
    m.on("load", () => {
      markReady(m);
      m.addSource("samples", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      const colour = ["match", ["get", "split"], ...Object.entries(SPLIT_COLOUR).flat(), "#555"] as unknown as maplibregl.ExpressionSpecification;
      m.addLayer({ id: "samples-fill", type: "fill", source: "samples", paint: { "fill-color": colour, "fill-opacity": 0.45 } });
      m.addLayer({ id: "samples-line", type: "line", source: "samples", paint: { "line-color": colour,
        "line-width": ["case", ["get", "locked_test"], 2.5, 1] } });
      m.addLayer({ id: "samples-dot", type: "circle", source: "samples", maxzoom: 6,
        paint: { "circle-radius": 3, "circle-color": colour, "circle-stroke-color": "#fff", "circle-stroke-width": 0.5 } });
      m.addSource("labels", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      m.addLayer({ id: "labels-line", type: "line", source: "labels", paint: { "line-color": "#D55E00", "line-width": 1.5 } });
      const pick = (e: maplibregl.MapLayerMouseEvent) => {
        const id = e.features?.[0]?.properties?.id;
        if (id) api.sample(id).then(setPicked).catch((err) => setError(err.message));
      };
      m.on("click", "samples-fill", pick); m.on("click", "samples-dot", pick);
    });
    map.current = m;
    return () => m.remove();
  }, []);

  useEffect(() => {
    const m = map.current;
    if (!m || !data) return;
    whenReady(m, () => {
      (m.getSource("samples") as maplibregl.GeoJSONSource).setData({ type: "FeatureCollection", features: data.features });
      m.setLayoutProperty("osm", "visibility", "visible");
    });
  }, [data]);

  useEffect(() => {
    const m = map.current;
    if (!m) return;
    whenReady(m, () => {
      (m.getSource("labels") as maplibregl.GeoJSONSource).setData({ type: "FeatureCollection",
        features: picked?.labels ? [{ type: "Feature", properties: {}, geometry: picked.labels }] : [] });
      if (picked) {
        const b = new maplibregl.LngLatBounds();
        picked.footprint.coordinates[0].forEach((c) => b.extend(c as [number, number]));
        m.fitBounds(b, { padding: 40, duration: 300 });
      }
    });
  }, [picked]);

  const d = datasets.find((x) => x.id === ds);
  return (
    <div className="split">
      <aside className="panel">
        <h1>Dataset explorer</h1>
        {error && <p className="error small">{error}</p>}
        {datasets.length > 1 && (
          <select value={ds ?? ""} onChange={(e) => { setDs(Number(e.target.value)); setPicked(null); }}>
            {datasets.map((x) => <option key={x.id} value={x.id}>{x.name} {x.version} ({x.split_name})</option>)}
          </select>
        )}
        {d && (
          <div className="card small">
            <b>{d.name} {d.version} · split {d.split_name}</b>
            <span>{d.samples} sites · state <span className={`badge ${d.state === "licence_review" ? "warn" : ""}`}>{human(d.state)}</span></span>
            <span>Licence {d.licence}: {d.licence_status}</span>
            <span>Split check: {d.split_check.ok ? "no leakage" : "LEAKAGE"} ({String(d.split_check.units_in_two_splits)} units
              across splits, {String(d.split_check.sites_within_buffer_across_splits)} pairs within 10 km)</span>
            <span>Hard negatives (FR-074): {d.negatives?.features ? Object.entries(d.negatives.by_type).filter(([, n]) => n)
              .map(([t, n]) => `${t.replace(/_/g, " ")} ${n}`).join(" · ") : d.negatives?.note ?? "none recorded"}
              {d.negatives?.problems?.length ? <span className="error"> · {d.negatives.problems.join("; ")}</span> : null}</span>
            <span className={d.overlaps.cross_split ? "error" : ""}>Overlaps: {d.overlaps.pairs} pairs, {d.overlaps.cross_split} across splits</span>
            <span className="muted">Test lock {d.test_lock_sha256.slice(0, 12) || "none"} · split {d.split_sha256.slice(0, 12)}</span>
          </div>
        )}
        {data && FILTERS.map((f) => (
          <label key={f} className="small">{human(f)}
            <select value={filters[f] ?? ""} onChange={(e) => setFilters((x) => ({ ...x, [f]: e.target.value }))}>
              <option value="">All</option>
              {(data.options[f] ?? []).map((o) => <option key={o} value={o}>{human(o || "unknown")}</option>)}
            </select>
          </label>
        ))}
        {data && (
          <div className="scroll"><table>
            <thead><tr><th>Split</th><th>Sites</th><th>Artisanal</th><th>Industrial</th><th>Africa</th></tr></thead>
            <tbody>{Object.entries(data.balance).map(([s, b]) => (
              <tr key={s}><td><span className="swatch" style={{ background: SPLIT_COLOUR[s] ?? "#555", display: "inline-block" }} /> {human(s)}</td>
                <td>{b.sites}</td><td>{b.mine_scale.Artisanal ?? 0}</td><td>{b.mine_scale.Industrial ?? 0}</td><td>{b.region.africa ?? 0}</td></tr>))}
            </tbody></table></div>
        )}
        <p className="small muted">Locked test sites have a thicker outline. Click a site for its labels and overlaps.</p>
        {picked && (
          <div className="card small">
            <b>{picked.site_id}</b>
            <span>{human(picked.split)}{picked.locked_test && " (locked test)"} · source split {picked.source_split || "—"}</span>
            <span>{picked.mine_scale} · {picked.mine_type} · {picked.region}</span>
            <span>Unit {picked.unit_id} · block {picked.block_id}</span>
            <span>Scene {picked.scene_id} ({picked.acquired})</span>
            <span>{picked.overlaps.length} overlap pair(s){picked.overlaps.some((o) => o.cross_split) && <b className="error"> including across splits</b>}</span>
          </div>
        )}
      </aside>
      <div ref={mapEl} className="map" />
    </div>
  );
}
