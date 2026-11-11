import { useEffect, useRef, useState } from "react";
import { api, Me } from "./api";
import { cache, FieldTask, queue, QueuedVisit } from "./fieldStore";
import { human } from "./status";

// FR-095: record field visits offline on a phone, then sync. Faces and number plates are blurred on the phone
// before a photo is stored; the server also strips all photo metadata.
type Photo = { blob: Blob; url: string; blurred: number };

function BlurEditor({ file, onDone }: { file: File; onDone: (p: Photo) => void }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const img = useRef<HTMLImageElement | null>(null);
  const [rects, setRects] = useState<[number, number, number, number][]>([]);
  const drag = useRef<[number, number] | null>(null);
  useEffect(() => {
    const im = new Image();
    im.onload = () => {
      img.current = im;
      const c = canvas.current!, scale = Math.min(1, 1600 / Math.max(im.width, im.height));
      c.width = Math.round(im.width * scale); c.height = Math.round(im.height * scale);
      draw([]);
    };
    im.src = URL.createObjectURL(file);
  }, [file]);
  const draw = (rs: typeof rects) => {
    const c = canvas.current!, g = c.getContext("2d")!, im = img.current!;
    g.filter = "none"; g.drawImage(im, 0, 0, c.width, c.height);
    for (const [x, y, w, h] of rs) {   // blur inside each rectangle, drawn from the original pixels
      g.save(); g.beginPath(); g.rect(x, y, w, h); g.clip(); g.filter = "blur(18px)"; g.drawImage(im, 0, 0, c.width, c.height); g.restore();
    }
  };
  const pos = (e: React.PointerEvent): [number, number] => {
    const r = canvas.current!.getBoundingClientRect();
    return [((e.clientX - r.left) / r.width) * canvas.current!.width, ((e.clientY - r.top) / r.height) * canvas.current!.height];
  };
  return (
    <div className="card small">
      <b>Blur people, faces and number plates</b>
      <span className="muted">Drag a box over each one. Only the blurred picture is kept.</span>
      <canvas ref={canvas} className="blur-canvas" onPointerDown={(e) => { drag.current = pos(e); }}
        onPointerUp={(e) => {
          if (!drag.current) return;
          const [x0, y0] = drag.current, [x1, y1] = pos(e); drag.current = null;
          if (Math.abs(x1 - x0) < 8 || Math.abs(y1 - y0) < 8) return;
          const next = [...rects, [Math.min(x0, x1), Math.min(y0, y1), Math.abs(x1 - x0), Math.abs(y1 - y0)] as [number, number, number, number]];
          setRects(next); draw(next);
        }} />
      <div className="actions wrap">
        <button onClick={() => { const n = rects.slice(0, -1); setRects(n); draw(n); }} disabled={!rects.length}>Undo box</button>
        <button className="primary" onClick={() => canvas.current!.toBlob((b) => b && onDone({ blob: b, url: URL.createObjectURL(b), blurred: rects.length }), "image/jpeg", 0.88)}>
          Keep photo ({rects.length} blurred)</button>
      </div>
    </div>
  );
}

export default function Field({ me }: { me: Me }) {
  const [tasks, setTasks] = useState<FieldTask[]>(cache.get("tasks") ?? []);
  const [reasons, setReasons] = useState<Record<string, string[]>>(cache.get("reasons") ?? {});
  const [online, setOnline] = useState(navigator.onLine);
  const [pending, setPending] = useState<QueuedVisit[]>([]);
  const [task, setTask] = useState<FieldTask | null>(null);
  const [notes, setNotes] = useState(""); const [observer, setObserver] = useState(me.username);
  const [position, setPosition] = useState<{ lat: number; lon: number; accuracy_m: number } | null>(null);
  const [decision, setDecision] = useState(""); const [reason, setReason] = useState("");
  const [photos, setPhotos] = useState<Photo[]>([]); const [editing, setEditing] = useState<File | null>(null);
  const [msg, setMsg] = useState(""); const [syncing, setSyncing] = useState(false);

  const refresh = () => queue.all().then(setPending).catch(() => setPending([]));
  const loadTasks = () => Promise.all([api.fieldTasks(), api.reviewOptions()]).then(([t, o]) => {
    setTasks(t); cache.set("tasks", t);
    const r = { field_confirm: o.field_confirm?.reasons ?? [], field_reject: o.field_reject?.reasons ?? [] };
    setReasons(r); cache.set("reasons", r); cache.set("tasks_at", new Date().toISOString());
  }).catch(() => setMsg("Offline: showing the tasks saved on this phone."));

  const sync = async () => {
    if (!navigator.onLine) return;
    setSyncing(true);
    for (const v of await queue.all()) {
      if (v.error) continue;                                            // refused before: needs the user, not a retry
      const r = await api.syncVisit(v).catch((e) => { setMsg(`Sync paused: ${(e as Error).message}`); return null; });
      if (r === null) break;                                            // connection dropped: try later
      if (r.ok) await queue.remove(v.client_uuid);
      else await queue.put({ ...v, error: r.detail });                  // refused by a rule: keep it, show why
    }
    setSyncing(false); refresh(); loadTasks();
  };
  useEffect(() => {
    refresh(); loadTasks().then(sync);
    const on = () => { setOnline(true); sync(); }, off = () => setOnline(false);
    window.addEventListener("online", on); window.addEventListener("offline", off);
    // The first request after reconnecting often fails; keep trying while visits wait and the phone is online.
    const t = setInterval(() => { if (navigator.onLine) queue.all().then((q) => { if (q.some((v) => !v.error)) sync(); }); }, 20000);
    return () => { window.removeEventListener("online", on); window.removeEventListener("offline", off); clearInterval(t); };
  }, []);

  const locate = () => navigator.geolocation?.getCurrentPosition(
    (p) => setPosition({ lat: p.coords.latitude, lon: p.coords.longitude, accuracy_m: Math.round(p.coords.accuracy) }),
    (e) => setMsg(`Position not available: ${e.message}`), { enableHighAccuracy: true, timeout: 20000 });
  const save = async () => {
    if (!task) return;
    const v: QueuedVisit = { client_uuid: crypto.randomUUID(), photos: photos.map((p) => p.blob), blurred: photos.map((p) => p.blurred),
      meta: { detection: task.id, visited_at: new Date().toISOString(), notes, observer_name: observer, ...(position ?? {}),
        ...(decision ? { decision: { action: decision, reason_code: reason } } : {}) } };
    await queue.put(v);
    setTask(null); setNotes(""); setPosition(null); setDecision(""); setReason(""); setPhotos([]);
    setMsg(navigator.onLine ? "Saved; syncing…" : "Saved on this phone. It will sync when you are online.");
    refresh(); sync();
  };

  return (
    <div className="page field-page">
      <h1>Field visits</h1>
      <p className="small"><span className={`badge ${online ? "" : "warn"}`}>{online ? "Online" : "Offline"}</span>{" "}
        {pending.length} visit(s) waiting to sync {online && pending.length > 0 && <button className="link" disabled={syncing} onClick={sync}>Sync now</button>}
        {cache.get<string>("tasks_at") && <span className="muted"> · tasks saved {cache.get<string>("tasks_at")!.slice(0, 16).replace("T", " ")}</span>}</p>
      {msg && <p className="small muted">{msg}</p>}
      {pending.filter((v) => v.error).map((v) => <p key={v.client_uuid} className="error small">Visit to #{String(v.meta.detection)} not accepted: {v.error}{" "}
        <button className="link" onClick={() => queue.remove(v.client_uuid).then(refresh)}>Discard</button></p>)}
      {!task ? (
        <ol className="queue">
          {tasks.map((t) => <li key={t.id} onClick={() => setTask(t)}><span className="grow"><b>#{t.id}</b> · {t.project} · {t.area_ha} ha
            <br /><span className="small muted">{t.lat.toFixed(5)}, {t.lon.toFixed(5)} · requested: {human(t.requested_because)}{t.request_note && ` (${t.request_note})`}
            {t.restricted && " · restricted location"}</span></span>
            <a className="small" href={`geo:${t.lat},${t.lon}`} onClick={(e) => e.stopPropagation()}>Navigate</a></li>)}
          {tasks.length === 0 && <li className="muted">No field checks requested.</li>}
        </ol>
      ) : (
        <div className="card">
          <h2>Visit to #{task.id}</h2>
          <span className="small muted">{task.project} · {task.lat.toFixed(5)}, {task.lon.toFixed(5)}</span>
          <button onClick={locate}>Record my position</button>
          {position && <span className="small">{position.lat.toFixed(5)}, {position.lon.toFixed(5)} (±{position.accuracy_m} m)</span>}
          <label>Observer<input value={observer} onChange={(e) => setObserver(e.target.value)} /></label>
          <label>Notes<textarea rows={3} value={notes} onChange={(e) => setNotes(e.target.value)}
            placeholder="What you saw. Describe disturbance only: not ownership, legality or minerals." /></label>
          <label>Photos<input type="file" accept="image/*" capture="environment" onChange={(e) => e.target.files?.[0] && setEditing(e.target.files[0])} /></label>
          {editing && <BlurEditor file={editing} onDone={(p) => { setPhotos((ps) => [...ps, p]); setEditing(null); }} />}
          <div className="row gap">{photos.map((p, i) => <img key={i} src={p.url} alt={`photo ${i + 1}`} className="field-thumb" />)}</div>
          <label>Decision (optional)<select value={decision} onChange={(e) => { setDecision(e.target.value); setReason(""); }}>
            <option value="">Evidence only, decide later</option><option value="field_confirm">Field confirm</option><option value="field_reject">Field reject</option></select></label>
          {decision && <label>Reason<select value={reason} onChange={(e) => setReason(e.target.value)}><option value="">Choose…</option>
            {(reasons[decision] ?? []).map((r) => <option key={r} value={r}>{human(r)}</option>)}</select></label>}
          <div className="actions wrap">
            <button className="primary" disabled={!!editing || (!!decision && !reason)} onClick={save}>Save visit</button>
            <button onClick={() => setTask(null)}>Cancel</button>
          </div>
        </div>
      )}
    </div>
  );
}
