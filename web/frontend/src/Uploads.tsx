import { useEffect, useRef, useState } from "react";
import { api, UploadInfo } from "./api";

// FR-022: upload a zipped Sentinel-2 L2A .SAFE in verified chunks. Network failures are retried with backoff;
// choosing the same file again (even after closing the page) resumes from the last byte the server confirmed.
const key = (f: File) => `minefoot-upload:${f.name}:${f.size}:${f.lastModified}`;
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

export default function Uploads({ projectId }: { projectId: number }) {
  const [list, setList] = useState<UploadInfo[]>([]);
  const [progress, setProgress] = useState<{ name: string; sent: number; size: number; note: string } | null>(null);
  const [error, setError] = useState("");
  const stop = useRef(false);
  const load = () => api.uploads(projectId).then(setList).catch((e) => setError(e.message));
  useEffect(() => { load(); return () => { stop.current = true; }; }, [projectId]);

  const send = async (file: File) => {
    setError(""); stop.current = false;
    let u: UploadInfo | null = null;
    try {
      const saved = (() => { try { return localStorage.getItem(key(file)); } catch { return null; } })();
      if (saved) u = await api.upload(Number(saved)).catch(() => null);
      if (!u || u.state !== "receiving" || u.size !== file.size) {
        u = await api.createUpload(projectId, file.name, file.size);
        try { localStorage.setItem(key(file), String(u.id)); } catch { /* resume then needs the same session */ }
      }
      let sent = u.received, failures = 0;
      while (sent < file.size && !stop.current) {
        setProgress({ name: file.name, sent, size: file.size, note: failures ? `retrying (attempt ${failures + 1})` : "" });
        const chunk = await file.slice(sent, sent + u.chunk_bytes).arrayBuffer();
        let r;
        try { r = await api.putChunk(u.id, sent, file.size, chunk); }
        catch { r = { status: 0, body: {} as UploadInfo & { detail?: string } }; }   // network down
        if (r.status === 200) { sent = r.body.received; failures = 0; u = r.body; continue; }
        if (r.status === 409 && typeof r.body.received === "number") { sent = r.body.received; continue; }
        if (r.status === 400 || r.status === 0 || r.status >= 500) {
          failures += 1;
          if (failures > 6) throw new Error(r.body.detail ?? "the connection keeps failing; choose the file again later to resume");
          await sleep(Math.min(30000, 1000 * 2 ** failures));
          sent = (await api.upload(u.id).catch(() => u!)).received;   // resume from what the server holds
          continue;
        }
        throw new Error(r.body.detail ?? `upload failed (${r.status})`);
      }
      if (u && sent >= file.size) {
        try { localStorage.removeItem(key(file)); } catch { /* nothing to clear */ }
        const done = await api.upload(u.id);
        if (done.state === "failed") setError(`${file.name}: ${done.error}`);
      }
    } catch (e) { setError((e as Error).message); }
    finally { setProgress(null); load(); }
  };

  return (
    <div className="card small">
      <h3>Upload imagery</h3>
      <p className="muted">A zipped Sentinel-2 Level-2A .SAFE folder (up to 4 GB), for example from the Copernicus Browser. Interrupted
        uploads resume: choose the same file again. Jobs use ready uploads alongside the catalogues.</p>
      <input type="file" accept=".zip" disabled={!!progress} onChange={(e) => e.target.files?.[0] && send(e.target.files[0])} />
      {progress && <div>
        <progress max={progress.size} value={progress.sent} style={{ width: "100%" }} />
        <span>{progress.name}: {(progress.sent / 1e6).toFixed(1)} of {(progress.size / 1e6).toFixed(1)} MB {progress.note}</span>
        <button className="link" onClick={() => { stop.current = true; }}>Pause</button>
      </div>}
      {list.length > 0 && <ul className="plain">{list.map((u) => <li key={u.id}>
        <b>{u.scene_id ?? u.filename}</b> · {u.state === "ready" ? `${u.acquired_at?.slice(0, 10)}, ${u.cloud_pct ?? "?"}% cloud, baseline ${u.processing_baseline}`
          : u.state === "receiving" ? `paused at ${Math.round((100 * u.received) / u.size)}%; choose the file again to resume` : <span className="error">{u.error}</span>}
      </li>)}</ul>}
      {error && <p className="error">{error}</p>}
    </div>
  );
}
