import type { StyleSpecification } from "maplibre-gl";

// OpenStreetMap for orientation only. Never label or judge from it: decisions use the dated
// Sentinel-2 scene shown in the review workspace (FRD 10.3).
export const BASE_STYLE: StyleSpecification = {
  version: 8,
  sources: {
    osm: {
      type: "raster", tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"], tileSize: 256, maxzoom: 19,
      attribution: "© OpenStreetMap contributors",
    },
  },
  layers: [
    { id: "background", type: "background", paint: { "background-color": "#20201e" } },
    { id: "osm", type: "raster", source: "osm", paint: { "raster-opacity": 0.85 } },
  ],
};

// Full-resolution raster tiles of a job (rio-tiler) above an overview image layer, from zoom 10.
// The source is replaced when the URL changes (another job or layer); `null` removes it.
export function setXYZ(m: import("maplibre-gl").Map, id: string, url: string | null | undefined, before: string) {
  const src = m.getSource(id) as unknown as { tiles?: string[] } | undefined;
  const want = url ? `${location.origin}${url}` : null;
  if (src && src.tiles?.[0] === want) return;
  if (m.getLayer(id)) m.removeLayer(id);
  if (src) m.removeSource(id);
  if (!want) return;
  m.addSource(id, { type: "raster", tiles: [want], tileSize: 256, minzoom: 10, maxzoom: 17 });
  m.addLayer({ id, type: "raster", source: id, minzoom: 10, paint: { "raster-resampling": "nearest", "raster-fade-duration": 0 } }, before);
}

// Run fn once the map's style has loaded. map.loaded() is false whenever tiles are still
// loading, even long after the load event, so it cannot be used for this.
const ready = new WeakSet<object>();
export function markReady(m: object) { ready.add(m); }
export function whenReady(m: { once: (ev: "load", fn: () => void) => unknown }, fn: () => void) {
  if (ready.has(m)) fn(); else m.once("load", fn);
}
