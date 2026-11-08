// Thin client for the Django API. Session cookie + CSRF header, same origin via the Vite proxy.

export type Me = { username: string; organisation: string; roles: string[]; privileged: boolean; mfa_enabled: boolean };
export type Project = { id: number; name: string; purpose: string; status: string; created_at: string; areas: number; detections: number };
export type Area = { id: number; name: string; area_km2: number; source: string; source_crs: string; geometry: GeoJSON.Geometry };
export type Scene = { id: number; source_id: string; provider: string; sensor: string; acquired_at: string; processing_baseline: string; cloud_pct: number | null; licence_note: string };
export type ReviewEntry = { action: string; reason_code: string; note: string; reviewer_role: string; status_before: string; status_after: string; at: string };
export type DetectionProps = {
  id: number; status: string; area_ha: number; score_band: string; score_type: string; threshold: number;
  min_mapping_unit_ha: number; model_version: string; masked_fraction: number; mask_status: string;
  evidence_grade: string; scene: Scene; statement: string; reviews?: ReviewEntry[];
};
export type DetectionFeature = GeoJSON.Feature<GeoJSON.Polygon, DetectionProps>;
export type ReviewOptions = Record<string, { status_after: string; roles: string[]; allowed_from: string[] | null; reasons: string[] }>;

function csrf(): string {
  return document.cookie.split("; ").find((c) => c.startsWith("csrftoken="))?.split("=")[1] ?? "";
}

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

async function call<T>(method: string, url: string, body?: unknown): Promise<T> {
  const isForm = body instanceof FormData;
  const res = await fetch(url, {
    method, credentials: "same-origin",
    headers: { ...(isForm || body === undefined ? {} : { "Content-Type": "application/json" }), "X-CSRFToken": csrf() },
    body: body === undefined ? undefined : isForm ? body : JSON.stringify(body),
  });
  if (res.status === 204) return undefined as T;
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const msg = data.detail ?? Object.entries(data).map(([k, v]) => `${k}: ${v}`).join("; ") ?? res.statusText;
    throw new ApiError(res.status, String(msg));
  }
  return data as T;
}

export const api = {
  csrf: () => call<{ csrfToken: string }>("GET", "/api/auth/csrf/"),
  login: (username: string, password: string) => call<Me>("POST", "/api/auth/login/", { username, password }),
  logout: () => call<void>("POST", "/api/auth/logout/"),
  me: () => call<Me>("GET", "/api/me/"),
  projects: () => call<{ results: Project[] }>("GET", "/api/projects/").then((r) => r.results),
  createProject: (name: string, purpose: string) =>
    call<Project>("POST", "/api/projects/", { name, purpose, acceptable_use: true }),
  archiveProject: (id: number) => call<Project>("POST", `/api/projects/${id}/archive/`),
  project: (id: number) => call<Project>("GET", `/api/projects/${id}/`),
  areas: (id: number) => call<Area[]>("GET", `/api/projects/${id}/areas/`),
  addArea: (id: number, name: string, geometry: GeoJSON.Geometry, source = "drawn") =>
    call<Area>("POST", `/api/projects/${id}/areas/`, { name, geometry, source }),
  uploadArea: (id: number, name: string, file: File) => {
    const f = new FormData(); f.append("name", name); f.append("file", file);
    return call<Area>("POST", `/api/projects/${id}/areas/`, f);
  },
  detections: (id: number) =>
    call<GeoJSON.FeatureCollection<GeoJSON.Polygon, DetectionProps>>("GET", `/api/projects/${id}/detections/`),
  detection: (id: number) => call<DetectionFeature>("GET", `/api/detections/${id}/`),
  review: (id: number, action: string, reason_code: string, note: string) =>
    call<DetectionFeature>("POST", `/api/detections/${id}/reviews/`, { action, reason_code, note }),
  reviewOptions: () => call<ReviewOptions>("GET", "/api/review-options/"),
  sceneBounds: (id: number, layer: string) =>
    call<{ bounds: [number, number, number, number]; date: string; scene_id: string; licence_note: string }>(
      "GET", `/api/scenes/${id}/image/?bounds=1&layer=${layer}`),
  sceneImageUrl: (id: number, layer: string) => `/api/scenes/${id}/image/?layer=${layer}`,
};
