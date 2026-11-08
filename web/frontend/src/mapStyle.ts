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

// Run fn once the map's style has loaded. map.loaded() is false whenever tiles are still
// loading, even long after the load event, so it cannot be used for this.
const ready = new WeakSet<object>();
export function markReady(m: object) { ready.add(m); }
export function whenReady(m: { once: (ev: "load", fn: () => void) => unknown }, fn: () => void) {
  if (ready.has(m)) fn(); else m.once("load", fn);
}
