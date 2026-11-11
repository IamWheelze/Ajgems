import maplibregl from "maplibre-gl";
import { useEffect, useRef, useState } from "react";
import { api, ErrorSite } from "./api";
import { BASE_STYLE, markReady, whenReady } from "./mapStyle";

// FR-129: evaluation error maps of a registered model on its locked test sites, worst pixel F1 first.
// Clicking a site moves the map to it. Each class has its own colour, label and toggle.
const CLASSES: { key: string; label: string; colour: string }[] = [
  { key: "hit", label: "Hit (true positive)", colour: "rgb(0,114,178)" },
  { key: "false_alarm", label: "False alarm (false positive)", colour: "rgb(230,159,0)" },
  { key: "missed", label: "Missed (false negative)", colour: "rgb(204,121,167)" },
  { key: "not_assessed", label: "Not assessed (hatched)", colour: "" },
];
const fmt = (v: number | null, d = 2) => (v === null ? "—" : v.toFixed(d));
const corners = (b: number[]): [[number, number], [number, number], [number, number], [number, number]] =>
  [[b[0], b[3]], [b[2], b[3]], [b[2], b[1]], [b[0], b[1]]];

export default function ErrorMaps({ modelId }: { modelId: number }) {
  const mapEl = useRef<HTMLDivElement>(null);
  const map = useRef<maplibregl.Map | null>(null);
  const [name, setName] = useState("");
  const [sites, setSites] = useState<ErrorSite[] | null>(null);
  const [site, setSite] = useState<string | null>(null);
  const [on, setOn] = useState<Record<string, boolean>>({ hit: true, false_alarm: true, missed: true, not_assessed: true });
  const [error, setError] = useState("");

  useEffect(() => {
    api.errorSites(modelId).then((r) => { setName(r.model); setSites(r.sites); if (r.sites.length) setSite(r.sites[0].site_id); })
      .catch((e) => setError(e.message));
  }, [modelId]);

  useEffect(() => {
    if (!mapEl.current) return;
    const m = new maplibregl.Map({ container: mapEl.current, style: BASE_STYLE, center: [0, 0], zoom: 2 });
    m.addControl(new maplibregl.NavigationControl({ showCompass: false }));
    m.addControl(new maplibregl.ScaleControl({ unit: "metric" }));
    m.on("load", () => { markReady(m); m.setLayoutProperty("osm", "visibility", "none"); });
    map.current = m;
    return () => m.remove();
  }, []);

  const classes = Object.entries(on).filter(([, v]) => v).map(([k]) => k).join(",") || "none";
  useEffect(() => {
    const m = map.current;
    if (!m || !site) return;
    let stale = false;
    whenReady(m, async () => {
      try {
        const [tc, er] = await Promise.all([api.errorBounds(modelId, site, "true_colour"), api.errorBounds(modelId, site, "errors")]);
        if (stale) return;
        const put = (id: string, url: string, b: number[]) => {
          const src = m.getSource(id) as maplibregl.ImageSource | undefined;
          if (src) src.updateImage({ url, coordinates: corners(b) });
          else {
            m.addSource(id, { type: "image", url, coordinates: corners(b) });
            m.addLayer({ id, type: "raster", source: id, paint: { "raster-resampling": "nearest", "raster-fade-duration": 0 } });
          }
        };
        put("tc", api.errorImageUrl(modelId, site, "true_colour"), tc.bounds);
        put("err", api.errorImageUrl(modelId, site, "errors", classes), er.bounds);
        m.fitBounds([[er.bounds[0], er.bounds[1]], [er.bounds[2], er.bounds[3]]], { padding: 30, duration: 300 });
        setError("");
      } catch (e) { setError((e as Error).message); }
    });
    return () => { stale = true; };
  }, [site, modelId]);
  useEffect(() => {   // class toggles only swap the overlay image
    const m = map.current;
    const src = m?.getSource("err") as maplibregl.ImageSource | undefined;
    if (m && src && site) api.errorBounds(modelId, site, "errors").then((er) => src.updateImage({ url: api.errorImageUrl(modelId, site, "errors", classes), coordinates: corners(er.bounds) }));
  }, [classes]);

  const cur = sites?.find((s) => s.site_id === site);
  return (
    <div className="split">
      <aside className="panel">
        <a href="#/models" className="small">← Model registry</a>
        <h1>Error maps · {name}</h1>
        <p className="small muted">Locked test sites, worst pixel F1 first. Internal research: labels are the dataset's, and incomplete labels make some "false alarms" real unlabelled mining.</p>
        {error && <p className="error small" role="alert">{error}</p>}
        {sites === null ? <p className="muted">Loading…</p> : sites.length === 0 ? <p className="muted">This run has no stored error maps.</p> : (
          <ol className="queue err-list" aria-label="Test sites by worst F1">
            {sites.map((s, i) => (
              <li key={s.site_id} className={s.site_id === site ? "active" : ""} onClick={() => setSite(s.site_id)}>
                <span className="muted small">{i + 1}</span>
                <img className="err-thumb" alt="" loading="lazy" src={api.errorImageUrl(modelId, s.site_id, "errors", "hit,false_alarm,missed")} />
                <span className="grow small"><b>{s.site_id}</b><br />F1 {fmt(s.f1)} · P {fmt(s.precision)} · R {fmt(s.recall)}<br />
                  <span className="muted">{s.mine_scale} · {s.mine_type} · {s.region}</span></span>
              </li>
            ))}
          </ol>
        )}
      </aside>
      <div className="err-map">
        <div ref={mapEl} className="map" />
        <div className="map-overlay small">
          <b>{cur ? `${cur.site_id} · F1 ${fmt(cur.f1)}` : "—"}</b>
          {cur && <span className="muted">{cur.tp.toLocaleString()} hit · {cur.fp.toLocaleString()} false-alarm · {cur.fn.toLocaleString()} missed pixels</span>}
          {CLASSES.map((c) => (
            <label key={c.key}><input type="checkbox" checked={on[c.key]} onChange={(e) => setOn({ ...on, [c.key]: e.target.checked })} />
              <span className={`swatch ${c.colour ? "" : "hatched"}`} style={{ background: c.colour || undefined }} /> {c.label}</label>
          ))}
          <span className="muted">Under: true colour, fixed stretch 0 to 0.35.</span>
        </div>
      </div>
    </div>
  );
}
