// FR-095 offline queue: visits (with photo blobs) wait in IndexedDB until the phone is online again.
export type QueuedVisit = { client_uuid: string; meta: Record<string, unknown>; photos: Blob[]; blurred: number[]; error?: string };
export type FieldTask = { id: number; project: string; lat: number; lon: number; area_ha: number; score_band: string; scene_date: string;
  requested_because: string; request_note: string; outline: GeoJSON.Geometry; restricted: boolean };

const DB = "minefoot-field", STORE = "queue";
function open(): Promise<IDBDatabase> {
  return new Promise((res, rej) => {
    const r = indexedDB.open(DB, 1);
    r.onupgradeneeded = () => r.result.createObjectStore(STORE, { keyPath: "client_uuid" });
    r.onsuccess = () => res(r.result); r.onerror = () => rej(r.error);
  });
}
async function tx<T>(mode: IDBTransactionMode, fn: (s: IDBObjectStore) => IDBRequest<T>): Promise<T> {
  const db = await open();
  return new Promise((res, rej) => {
    const r = fn(db.transaction(STORE, mode).objectStore(STORE));
    r.onsuccess = () => res(r.result); r.onerror = () => rej(r.error);
  });
}
export const queue = {
  all: () => tx<QueuedVisit[]>("readonly", (s) => s.getAll() as IDBRequest<QueuedVisit[]>),
  put: (v: QueuedVisit) => tx("readwrite", (s) => s.put(v)),
  remove: (id: string) => tx("readwrite", (s) => s.delete(id)),
};
// Small JSON caches (tasks, reasons) so the page works after a restart without a connection.
export const cache = {
  get<T>(k: string): T | null { try { const v = localStorage.getItem(`minefoot-field:${k}`); return v ? JSON.parse(v) : null; } catch { return null; } },
  set(k: string, v: unknown) { try { localStorage.setItem(`minefoot-field:${k}`, JSON.stringify(v)); } catch { /* storage full or blocked */ } },
};
