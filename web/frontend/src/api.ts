// Thin client for the Django API. Session cookie + CSRF header, same origin via the Vite proxy.

export type Me = { username: string; organisation: string; roles: string[]; privileged: boolean; mfa_enabled: boolean;
  mfa_satisfied: boolean };
export type Project = { id: number; name: string; purpose: string; status: string; created_at: string; areas: number; detections: number; intended_use: string };
export type Area = { id: number; name: string; area_km2: number; source: string; source_crs: string; geometry: GeoJSON.Geometry };
export type Scene = { id: number; source_id: string; provider: string; sensor: string; acquired_at: string; processing_baseline: string; cloud_pct: number | null; licence_note: string };
export type ReviewEntry = { action: string; reason_code: string; note: string; reviewer_role: string; status_before: string; status_after: string; at: string };
export type DetectionProps = {
  id: number; status: string; area_ha: number; score_band: string; score_type: string; threshold: number;
  min_mapping_unit_ha: number; model_version: string; masked_fraction: number; mask_status: string;
  evidence_grade: string; scene: Scene; statement: string; reviews?: ReviewEntry[]; restricted?: boolean;
  conditions?: { condition: string; share: number; message: string }[];
};
export type PixelInfo = { lon: number; lat: number; crs: string; row: number; col: number; tile: string;
  reflectance: Record<string, number | null>; ndvi: number | null; ndwi: number | null; scl: number; scl_class: string; usable: boolean;
  suspect_bright: boolean; model_score: number | null; threshold: number;
  scene: { id: string; provider: string; date: string; processing_baseline: string } | null };
export type ErrorSite = { site_id: string; f1: number | null; precision: number | null; recall: number | null; mine_scale: string;
  mine_type: string; region: string; false_candidates: number; label_footprints: number; tp: number; fp: number; fn: number };
export type PoolFeature = GeoJSON.Feature<GeoJSON.Polygon, { id: number; project: number; detection: number | null; missed_site: number | null;
  kind: string; negative_type: string; scene_id: string; scene_date: string | null; status_at_nomination: string; protocol_version: string;
  nominated_by: string; nominated_at: string; note: string; state: string; can_check: boolean | null;
  decision: { decision: string; reason_code: string; note: string; checker: string; at: string } | null }>;
export type UploadInfo = { id: number; project: number; filename: string; size: number; received: number; state: string; sha256: string;
  error: string; scene_id: string | null; acquired_at: string | null; cloud_pct: number | null; processing_baseline: string | null;
  chunk_bytes: number; created_at: string };
export type AnnotationTask = { id: number; project: number; job: number; tile: string; tile_id: number; scene: string | null; date: string | null;
  state: string; protocol_version: string; created_by: string; versions: number; labels: GeoJSON.FeatureCollection; classes: string[] };
export type Correction = { id: number; detection: number; project: number; kind: string; description: string; contact: string;
  requested_by: string; owner: string | null; state: string; outcome: string; review: number | null; created_at: string;
  events: { action: string; state_after: string; actor: string; note: string; at: string }[] };
export type MonitoringReport = {
  groups: { area: string; season: string; jobs: number[]; tiles: number; model_runs: string[]; usable_km2: number; input_shift: Record<string, number>;
    candidate_km2_per_1000km2: number | null; reference_km2_per_1000km2: number | null; output_ratio: number | null;
    reviewed: number; rejection_rate: number | null }[];
  alerts: { area: string; season: string; kind: string; message: string }[];
  thresholds: { input_shift_max_sd: number; output_ratio_range: [number, number]; output_min_usable_km2: number;
    rejection_rate_max: number; rejection_min_reviewed: number } };
export type Cost = { seconds: number; storage_bytes: number; money: { compute: number; storage_per_month: number; currency: string } | null };
export type Domain = { model_run: string; test_sites: number; flags: string[]; inside: boolean | null; rule: string;
  nearest_test_site?: string; nearest_test_km?: number; nearest_region?: string; same_season_test_sites?: number };
export type MissedProps = { id: number; job: number; area_ha: number; reason_code: string; note: string; tile_outcome: string;
  scene_date: string | null; added_by: string; at: string; counts_as: string };
export type DetectionFeature = GeoJSON.Feature<GeoJSON.Polygon, DetectionProps>;
export type ModelCard = { model_version: string; threshold: number; score_type: string; trained_on?: string;
  global_test_f1?: number; global_test_f1_ci95?: [number, number]; tested_in_nigeria: boolean; domain_warning: string | null };
export type Job = {
  id: number; project: number; area: number; state: string; model_version: string; message: string; failure_reason: string;
  created_at: string; started_at: string | null; finished_at: string | null; candidates: number;
  parameters: Record<string, unknown>;
  estimate: { tiles: number; tiles_with_imagery: number; scenes_found: number; clearest_scene: string; clearest_cloud_pct: number;
    estimated_minutes: number; estimate_basis: string; area_km2: number; model: ModelCard;
    source?: string; sources_not_approved?: Record<string, string>; domain?: Domain;
    cost?: Cost & { seconds_per_tile: number; bytes_per_tile: number; basis: string };
    quota?: { ok: boolean; reason?: string; km2_this_month?: number; jobs_this_month?: number; storage_gb?: number } };
  actual?: Cost & { tiles_processed?: number; seconds_vs_estimate?: number | null };
  tiles: { total: number; by_state: Record<string, number>; by_outcome: Record<string, number> };
  progress?: { elapsed_s: number; tiles_done: number; remaining_s: number | null; bytes_fetched: number; compute: string;
    area_coverage: { usable_pct: number | null; not_assessed_pct: number | null } | null };
};
export type TileProps = { grid_id: string; state: string; outcome: string; usable_pct: number | null; quality_decision: string;
  candidates: number; failure_reason: string; scene: string | null; date: string | null; checksums?: Record<string, string>; tile_id?: number;
  conditions?: { condition: string; share: number; message: string }[] };
export type ModelVersion = {
  id: number; name: string; state: string; input_schema: string; code_commit: string; threshold: number;
  dataset: Record<string, string>; licences: { item: string; licence: string }[];
  metrics: Record<string, Record<string, number | null>>; card: Record<string, string | boolean>;
  registered_at: string; registered_by: string; proposed_by: string | null; approved_by: string | null;
  approved_at: string | null; previous_production: string | null;
  reports: { id: number; split: string; baseline: string | null; sites: number; created_at: string;
    result: { metrics?: Record<string, { a: number | null; b: number | null; b_minus_a: number | null; ci95: [number | null, number | null]; verdict: string }>; note?: string } }[];
};
type Stratum = Record<string, number | null> & { sites: number; ci95: Record<string, [number, number]> };
export type RunDashboard = { id: number; name: string; state: string; run: string; missing: string[];
  history: { epoch: number; train_loss: number; val_chip_loss: number; lr: number; checkpoint: boolean }[];
  metrics: { best_epoch: number | null; epochs: number | null; runtime_s: number | null; threshold_score: number;
    strata: Record<"val" | "test", Record<string, Stratum>> } | null;
  curves: { created_at: string } & Record<"val" | "test", {
    at_threshold: { precision: number | null; recall: number | null; f1: number | null };
    pr: { level: number; score: number; precision: number | null; recall: number }[];
    reliability: { score_from: number; score_to: number; pixels: number; mean_score: number | null; labelled_fraction: number | null }[] }> | null };
export type DatasetInfo = { id: number; name: string; version: string; state: string; licence: string; licence_status: string;
  source: string; split_name: string; split_sha256: string; test_lock_sha256: string; samples: number;
  split_check: Record<string, number | boolean>; overlaps: { pairs: number; cross_split: number; by_kind: Record<string, number> };
  negatives?: { features: number; by_type: Record<string, number>; problems?: string[]; note?: string; source?: string } };
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
  createProject: (name: string, purpose: string, intended_use = "internal_research") =>
    call<Project>("POST", "/api/projects/", { name, purpose, intended_use, acceptable_use: true }),
  archiveProject: (id: number) => call<Project>("POST", `/api/projects/${id}/archive/`),
  project: (id: number) => call<Project>("GET", `/api/projects/${id}/`),
  areas: (id: number) => call<Area[]>("GET", `/api/projects/${id}/areas/`),
  addArea: (id: number, name: string, geometry: GeoJSON.Geometry, source = "drawn") =>
    call<Area>("POST", `/api/projects/${id}/areas/`, { name, geometry, source }),
  uploadArea: (id: number, name: string, file: File) => {
    const f = new FormData(); f.append("name", name); f.append("file", file);
    return call<Area>("POST", `/api/projects/${id}/areas/`, f);
  },
  detections: (id: number, filters: Record<string, string> = {}) => {
    const q = new URLSearchParams(Object.entries(filters).filter(([, v]) => v)).toString();
    return call<GeoJSON.FeatureCollection<GeoJSON.Polygon, DetectionProps>>("GET", `/api/projects/${id}/detections/${q ? `?${q}` : ""}`);
  },
  missedSites: (id: number) => call<GeoJSON.FeatureCollection<GeoJSON.Polygon, MissedProps> & { reasons: string[] }>("GET", `/api/projects/${id}/missed-sites/`),
  addMissedSite: (id: number, geometry: GeoJSON.Polygon, reason_code: string, note: string) =>
    call<GeoJSON.Feature<GeoJSON.Polygon, MissedProps>>("POST", `/api/projects/${id}/missed-sites/`, { geometry, reason_code, note }),
  pixel: (id: number, lon: number, lat: number) => call<PixelInfo>("GET", `/api/detections/${id}/pixel/?lon=${lon}&lat=${lat}`),
  jobPreview: (id: number, threshold: number, mmu_ha: number) =>
    call<GeoJSON.FeatureCollection & { summary: { candidates: number; kept: number; added: number; removed: number; area_ha: number; job_candidates: number } }>(
      "GET", `/api/jobs/${id}/preview/?threshold=${threshold}&mmu_ha=${mmu_ha}`),
  jobRethreshold: (id: number, threshold: number, mmu_ha: number) => call<Job>("POST", `/api/jobs/${id}/rethreshold/`, { threshold, mmu_ha }),
  errorSites: (id: number) => call<{ model: string; split: string; sites: ErrorSite[] }>("GET", `/api/models/${id}/errors/`),
  errorBounds: (id: number, site: string, layer: string) => call<{ bounds: number[] }>("GET", `/api/models/${id}/errors/${encodeURIComponent(site)}/image/?layer=${layer}&bounds=1`),
  errorImageUrl: (id: number, site: string, layer: string, classes = "") =>
    `/api/models/${id}/errors/${encodeURIComponent(site)}/image/?layer=${layer}${classes ? `&classes=${classes}` : ""}`,
  pool: () => call<{ features: PoolFeature[]; decisions: Record<string, string[]> }>("GET", "/api/pool/"),
  nominate: (detection: number, note = "") => call<PoolFeature>("POST", "/api/pool/", { detection, note }),
  poolDecide: (id: number, decision: string, reason_code: string, note: string) => call<PoolFeature>("POST", `/api/pool/${id}/decide/`, { decision, reason_code, note }),
  poolBounds: (id: number) => call<{ bounds: number[] }>("GET", `/api/pool/${id}/image/?bounds=1`),
  poolImageUrl: (id: number) => `/api/pool/${id}/image/`,
  restrict: (id: number, restricted: boolean, reason: string) => call<{ id: number; restricted: boolean }>("POST", `/api/detections/${id}/restrict/`, { restricted, reason }),
  restrictedGrants: (projectId: number) => call<{ id: number; user: string; granted_by: string; reason: string; granted_at: string; revoked_at: string | null }[]>(
    "GET", `/api/projects/${projectId}/restricted-grants/`),
  grantRestricted: (projectId: number, user: string, reason: string, revoke = false) =>
    call<unknown>("POST", `/api/projects/${projectId}/restricted-grants/`, { user, reason, revoke }),
  areaDomain: (id: number) => call<Domain>("GET", `/api/areas/${id}/domain/`),
  uploads: (projectId: number) => call<UploadInfo[]>("GET", `/api/projects/${projectId}/uploads/`),
  createUpload: (projectId: number, filename: string, size: number) => call<UploadInfo>("POST", `/api/projects/${projectId}/uploads/`, { filename, size }),
  upload: (id: number) => call<UploadInfo>("GET", `/api/uploads/${id}/`),
  /** One verified chunk (FR-022). Resolves with the server state; a 409 carries the offset to resume from. */
  putChunk: async (id: number, start: number, total: number, chunk: ArrayBuffer): Promise<{ status: number; body: UploadInfo & { detail?: string } }> => {
    const digest = await crypto.subtle.digest("SHA-256", chunk);
    const hex = [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
    const res = await fetch(`/api/uploads/${id}/`, { method: "PUT", credentials: "same-origin", body: chunk, headers: {
      "Content-Type": "application/octet-stream", "X-CSRFToken": csrf(), "X-Chunk-SHA256": hex,
      "Content-Range": `bytes ${start}-${start + chunk.byteLength - 1}/${total}` } });
    return { status: res.status, body: await res.json().catch(() => ({})) };
  },
  fieldTasks: () => call<import("./fieldStore").FieldTask[]>("GET", "/api/field/tasks/"),
  /** Sync one queued visit (FR-095). Resolves ok/detail; rejects only when the network fails. */
  syncVisit: async (v: import("./fieldStore").QueuedVisit): Promise<{ ok: boolean; detail?: string }> => {
    const f = new FormData();
    f.append("meta", JSON.stringify({ ...v.meta, client_uuid: v.client_uuid }));
    f.append("blurred", JSON.stringify(v.blurred));
    v.photos.forEach((b, i) => f.append("photos", b, `photo_${i}.jpg`));
    const res = await fetch("/api/field/visits/", { method: "POST", body: f, credentials: "same-origin", headers: { "X-CSRFToken": csrf() } });
    if (res.ok) return { ok: true };
    const d = await res.json().catch(() => ({}));
    if (res.status >= 500) throw new Error("server error");
    if (res.status === 401 || res.status === 403) throw new Error(`sign in again to sync (${d.detail ?? res.status})`);   // keep queued
    return { ok: false, detail: d.detail ?? `refused (${res.status})` };
  },
  fieldVisits: (id: number) => call<{ id: number; observer: string; observer_name: string; visited_at: string; lat: number | null; lon: number | null;
    accuracy_m: number | null; notes: string; decision: { action: string; reason_code: string } | null;
    photos: { id: number; blurred_regions: number }[] }[]>("GET", `/api/detections/${id}/field-visits/`),
  annotationTasks: () => call<Omit<AnnotationTask, "labels" | "classes">[]>("GET", "/api/annotation-tasks/"),
  createAnnotationTask: (tile: number) => call<AnnotationTask>("POST", "/api/annotation-tasks/", { tile }),
  annotationTask: (id: number) => call<AnnotationTask>("GET", `/api/annotation-tasks/${id}/`),
  annotationEdit: (id: number, body: Record<string, unknown>) => call<GeoJSON.FeatureCollection>("POST", `/api/annotation-tasks/${id}/edit/`, body),
  annotationState: (id: number, state: string) => call<AnnotationTask>("POST", `/api/annotation-tasks/${id}/state/`, { state }),
  annotationBounds: (id: number, layer: string) => call<{ bounds: number[] }>("GET", `/api/annotation-tasks/${id}/image/?layer=${layer}&bounds=1`),
  annotationImageUrl: (id: number, layer: string) => `/api/annotation-tasks/${id}/image/?layer=${layer}`,
  signingKey: () => call<{ key_id: string; public_key: string; development_key: boolean; pem: string }>("GET", "/api/signing-key/"),
  verifyExport: (f: File) => { const d = new FormData(); d.append("package", f); return call<{ ok: boolean; problems: string[]; files_checked: number }>("POST", "/api/exports/verify/", d); },
  corrections: () => call<Correction[]>("GET", "/api/corrections/"),
  requestCorrection: (detection: number, kind: string, description: string) => call<Correction>("POST", "/api/corrections/", { detection, kind, description }),
  correctionAction: (id: number, action: string, body: Record<string, unknown>) => call<Correction>("POST", `/api/corrections/${id}/${action}/`, body),
  monitoring: () => call<MonitoringReport>("GET", "/api/monitoring/"),
  detection: (id: number) => call<DetectionFeature>("GET", `/api/detections/${id}/`),
  review: (id: number, action: string, reason_code: string, note: string, geometry?: GeoJSON.Polygon) =>
    call<DetectionFeature>("POST", `/api/detections/${id}/reviews/`, { action, reason_code, note, ...(geometry ? { geometry } : {}) }),
  reviewOptions: () => call<ReviewOptions>("GET", "/api/review-options/"),
  sceneBounds: (id: number, layer: string) =>
    call<{ bounds: [number, number, number, number]; date: string; scene_id: string; licence_note: string }>(
      "GET", `/api/scenes/${id}/image/?bounds=1&layer=${layer}`),
  sceneImageUrl: (id: number, layer: string) => `/api/scenes/${id}/image/?layer=${layer}`,
  detectionBounds: (id: number, layer: string, job: number | null = null) =>
    call<{ bounds: [number, number, number, number]; date: string; scene_id: string; licence_note: string }>(
      "GET", `/api/detections/${id}/image/?bounds=1&layer=${layer}${job ? `&job=${job}` : ""}`),
  detectionImageUrl: (id: number, layer: string, stretch = "", job: number | null = null) =>
    `/api/detections/${id}/image/?layer=${layer}${stretch ? `&stretch=${stretch}` : ""}${job ? `&job=${job}` : ""}`,
  detectionDates: (id: number) => call<{ current: { job: number; date: string; scene_id: string }; others: { job: number; date: string; scene_id: string; cloud_pct: number | null }[] }>("GET", `/api/detections/${id}/dates/`),
  editGeometry: (id: number) => call<GeoJSON.Polygon & { vertices: number; original_vertices: number; valid: boolean }>(
    "GET", `/api/detections/${id}/edit-geometry/`),
  exportPackage: (projectId: number, format: string, statuses: string[], job?: number) =>
    download(`/api/projects/${projectId}/exports/`, { format, statuses, ...(job ? { job } : {}) },
      `project_${projectId}_${format}_export.zip`),
  models: () => call<ModelVersion[]>("GET", "/api/models/"),
  modelDashboard: (id: number) => call<RunDashboard>("GET", `/api/models/${id}/dashboard/`),
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
