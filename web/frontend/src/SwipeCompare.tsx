import maplibregl from "maplibre-gl";
import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import { BASE_STYLE, markReady, whenReady } from "./mapStyle";
import { STATUS } from "./status";

// FR-127 (MVP swipe): a second, non-interactive map stacked over the main one and clipped at the
// slider. It copies the main map's camera on every move, so both sides stay in the same place and zoom.
// The main map keeps every interaction (clicks, editing) because the top map ignores the pointer.
export default function SwipeCompare({ main, detectionId, layer, label, outline, all, showNA }: {
  main: maplibregl.Map; detectionId: number; layer: string; label: string;
  outline: GeoJSON.Feature | null; all: GeoJSON.FeatureCollection; showNA: boolean;
}) {
  const el = useRef<HTMLDivElement>(null);
  const top = useRef<maplibregl.Map | null>(null);
  const [pos, setPos] = useState(50);   // slider position, % from the left
  const [error, setError] = useState("");
  const showNARef = useRef(showNA);

  useEffect(() => {
    if (!el.current) return;
    const m = new maplibregl.Map({
      container: el.current, style: BASE_STYLE, interactive: false, attributionControl: false,
      center: main.getCenter(), zoom: main.getZoom(), bearing: main.getBearing(), pitch: main.getPitch(),
    });
    m.on("load", () => {
      markReady(m);
      m.setLayoutProperty("osm", "visibility", "none");
      m.addSource("dets", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      for (const [s, st] of Object.entries(STATUS))   // same status styling as the main map
        m.addLayer({ id: `dets-${s}`, type: "line", source: "dets", filter: ["==", ["get", "status"], s],
          paint: { "line-color": st.colour, "line-width": 2, ...(st.dash ? { "line-dasharray": st.dash } : {}) } });
      m.addSource("sel", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      m.addLayer({ id: "sel", type: "line", source: "sel", paint: { "line-color": "#ffffff", "line-width": 3 } });
    });
    const sync = () => m.jumpTo({ center: main.getCenter(), zoom: main.getZoom(), bearing: main.getBearing(), pitch: main.getPitch() });
    main.on("move", sync);
    const ro = new ResizeObserver(() => m.resize());
    ro.observe(el.current);
    top.current = m;
    if (import.meta.env.DEV) (window as unknown as { __swipeMap: maplibregl.Map }).__swipeMap = m;   // browser tests only
    return () => { main.off("move", sync); ro.disconnect(); m.remove(); top.current = null; };
  }, [main]);

  useEffect(() => {
    const m = top.current;
    if (!m) return;
    whenReady(m, () => (m.getSource("sel") as maplibregl.GeoJSONSource)
      .setData({ type: "FeatureCollection", features: outline ? [outline] : [] }));
  }, [outline]);

  useEffect(() => {
    const m = top.current;
    if (m) whenReady(m, () => (m.getSource("dets") as maplibregl.GeoJSONSource).setData(all));
  }, [all]);

  useEffect(() => {
    const m = top.current;
    if (!m) return;
    let stale = false;
    whenReady(m, async () => {
      try {
        const meta = await api.detectionBounds(detectionId, layer);
        if (stale) return;
        const [w, s, e, n] = meta.bounds;
        const coordinates: [[number, number], [number, number], [number, number], [number, number]] = [[w, n], [e, n], [e, s], [w, s]];
        const put = (id: string, url: string, coordinates: [[number, number], [number, number], [number, number], [number, number]]) => {
          const src = m.getSource(id) as maplibregl.ImageSource | undefined;
          if (src) src.updateImage({ url, coordinates });
          else {
            m.addSource(id, { type: "image", url, coordinates });
            m.addLayer({ id, type: "raster", source: id, paint: { "raster-resampling": "nearest", "raster-fade-duration": 0 } }, "dets-unreviewed");
          }
        };
        put("scene", api.detectionImageUrl(detectionId, layer), coordinates);
        // FR-125 on this side too: unassessed ground is hatched, never shown as clean.
        const na = await api.detectionBounds(detectionId, "not_assessed");
        if (stale) return;
        const [nw, ns, ne, nn] = na.bounds;
        put("na", api.detectionImageUrl(detectionId, "not_assessed"), [[nw, nn], [ne, nn], [ne, ns], [nw, ns]]);
        m.setLayoutProperty("na", "visibility", showNARef.current ? "visible" : "none");
        setError("");
      } catch (e) { setError((e as Error).message); }
    });
    return () => { stale = true; };
  }, [detectionId, layer]);

  useEffect(() => {
    showNARef.current = showNA;
    const m = top.current;
    if (m && m.getLayer("na")) m.setLayoutProperty("na", "visibility", showNA ? "visible" : "none");
  }, [showNA]);

  const startDrag = (e: React.PointerEvent) => {
    const box = el.current!.parentElement!.getBoundingClientRect();
    const move = (ev: PointerEvent) => setPos(Math.min(98, Math.max(2, ((ev.clientX - box.left) / box.width) * 100)));
    const up = () => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up); };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    e.preventDefault();
  };

  return (
    <>
      <div ref={el} className="map swipe-top" style={{ clipPath: `inset(0 0 0 ${pos}%)` }} aria-hidden="true" />
      <div className="swipe-handle" style={{ left: `${pos}%` }} onPointerDown={startDrag}
        role="slider" aria-label="Swipe position" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(pos)} tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === "ArrowLeft") setPos((p) => Math.max(2, p - 5));
          if (e.key === "ArrowRight") setPos((p) => Math.min(98, p + 5));
        }}>
        <span className="swipe-grip">⇔</span>
      </div>
      <div className="swipe-label small">{label}{error && <span className="error"> · {error}</span>}</div>
    </>
  );
}
