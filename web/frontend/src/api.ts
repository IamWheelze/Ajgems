// Thin client for the Django API. Session cookie + CSRF header, same origin via the Vite proxy.

export type Me = { username: string; organisation: string; roles: string[]; privileged: boolean; mfa_enabled: boolean;
  mfa_satisfied: boolean };
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
export type ModelCard = { model_version: string; threshold: number; score_type: string; trained_on?: string;
  global_test_f1?: number; global_test_f1_ci95?: [number, number]; tested_in_nigeria: boolean; domain_warning: string | null };
export type Job = {
  id: number; project: number; area: number; state: string; model_version: string; message: string; failure_reason: string;
  created_at: string; started_at: string | null; finished_at: string | null; candidates: number;
  parameters: Record<string, unknown>;
  estimate: { tiles: number; tiles_with_imagery: number; scenes_found: number; clearest_scene: string; clearest_cloud_pct: number;
    estimated_minutes: number; estimate_basis: string; area_km2: number; model: ModelCard };
  tiles: { total: number; by_state: Record<string, number>; by_outcome: Record<string, number> };
};
export type TileProps = { grid_id: string; state: string; outcome: string; usable_pct: number | null; quality_decision: string;
  candidates: number; failure_reason: string; scene: string | null; date: string | null };
export type ModelVersion = {
  id: number; name: string; state: string; input_schema: string; code_commit: string; threshold: number;
  dataset: Record<string, string>; licences: { item: string; licence: string }[];
  metrics: Record<string, Record<string, number | null>>; card: Record<string, string | boolean>;
  registered_at: string; registered_by: string; proposed_by: string | null; approved_by: string | null;
  approved_at: string | null; previous_production: string | null;
  reports: { id: number; split: string; baseline: string | null; sites: number; created_at: string;
    result: { metrics?: Record<string, { a: number | null; b: number | null; b_minus_a: number | null; ci95: [number | null, number | null]; verdict: string }>; note?: string } }[];
};
export type DatasetInfo = { id: number; name: string; version: string; state: string; licence: string; licence_status: string;
  source: string; split_name: string; split_sha256: string; test_lock_sha256: string; samples: number;
  split_check: Record<string, number | boolean>; overlaps: { pairs: number; cross_split: number; by_kind: Record<string, number> } };
export type SampleProps = { id: number; site_id: string; split: string; mine_scale: string; mine_type: string; region: string; locked_test: boolean };
export type SampleDetail = SampleProps & { source_split: string; unit_id: string; block_id: string; scene_id: string; acquired: string | null;
  footprint: GeoJSON.Polygon; labels: GeoJSON.MultiPolygon | null; overlaps: { other: string; kind: string; cross_split: boolean; iou: number | null }[] };
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

async function download(url: string, body: unknown, filename: string): Promise<void> {
  const res = await fetch(url, { method: "POST", credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() }, body: JSON.stringify(body) });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new ApiError(res.status, String(data.detail ?? res.statusText));
  }
  const blob = await res.blob();
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob); a.download = filename; a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 5000);
}

export const api = {
  csrf: () => call<{ csrfToken: string }>("GET", "/api/auth/csrf/"),
  login: (username: string, password: string) =>
    call<Me | { mfa_required: true }>("POST", "/api/auth/login/", { username, password }),
  mfaVerify: (code: string) => call<Me>("POST", "/api/auth/mfa/verify/", { code }),
  mfaSetup: () => call<{ secret: string; otpauth_uri: string; qr_png: string }>("POST", "/api/auth/mfa/setup/"),
  mfaConfirm: (code: string) => call<Me>("POST", "/api/auth/mfa/confirm/", { code }),
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
  review: (id: number, action: string, reason_code: string, note: string, geometry?: GeoJSON.Polygon) =>
    call<DetectionFeature>("POST", `/api/detections/${id}/reviews/`, { action, reason_code, note, ...(geometry ? { geometry } : {}) }),
  reviewOptions: () => call<ReviewOptions>("GET", "/api/review-options/"),
  sceneBounds: (id: number, layer: string) =>
    call<{ bounds: [number, number, number, number]; date: string; scene_id: string; licence_note: string }>(
      "GET", `/api/scenes/${id}/image/?bounds=1&layer=${layer}`),
  sceneImageUrl: (id: number, layer: string) => `/api/scenes/${id}/image/?layer=${layer}`,
  detectionBounds: (id: number, layer: string) =>
    call<{ bounds: [number, number, number, number]; date: string; scene_id: string; licence_note: string }>(
      "GET", `/api/detections/${id}/image/?bounds=1&layer=${layer}`),
  detectionImageUrl: (id: number, layer: string) => `/api/detections/${id}/image/?layer=${layer}`,
  editGeometry: (id: number) => call<GeoJSON.Polygon & { vertices: number; original_vertices: number; valid: boolean }>(
    "GET", `/api/detections/${id}/edit-geometry/`),
  exportPackage: (projectId: number, format: string, statuses: string[], job?: number) =>
    download(`/api/projects/${projectId}/exports/`, { format, statuses, ...(job ? { job } : {}) },
      `project_${projectId}_${format}_export.zip`),
  models: () => call<ModelVersion[]>("GET", "/api/models/"),
  modelAction: (id: number, action: string) => call<ModelVersion>("POST", `/api/models/${id}/${action}/`, {}),
  datasets: () => call<DatasetInfo[]>("GET", "/api/datasets/"),
  samples: (id: number, q: string) => call<GeoJSON.FeatureCollection<GeoJSON.Polygon, SampleProps> & {
    balance: Record<string, { sites: number; mine_scale: Record<string, number>; mine_type: Record<string, number>; region: Record<string, number> }>;
    options: Record<string, string[]> }>("GET", `/api/datasets/${id}/samples/${q}`),
  sample: (id: number) => call<SampleDetail>("GET", `/api/samples/${id}/`),
  jobs: (projectId: number) => call<Job[]>("GET", `/api/projects/${projectId}/jobs/`),
  estimateJob: (projectId: number, area: number, start: string, end: string, max_cloud: number) =>
    call<Job>("POST", `/api/projects/${projectId}/jobs/`, { area, start, end, max_cloud }),
  job: (id: number) => call<Job>("GET", `/api/jobs/${id}/`),
  jobTiles: (id: number) => call<GeoJSON.FeatureCollection<GeoJSON.Polygon, TileProps>>("GET", `/api/jobs/${id}/tiles/`),
  jobAction: (id: number, action: "start" | "cancel" | "resume") => call<Job>("POST", `/api/jobs/${id}/${action}/`),
};
