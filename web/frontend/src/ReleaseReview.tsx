import { useEffect, useState } from "react";
import { api, ReleaseReview as RR } from "./api";

// FR-104: the second person's decision. They see every open issue and the evidence, choose the scope they approve
// for, and write a reason for each open issue. The server enforces the same rules; this page explains them.
const SCOPE_LABEL: Record<string, string> = { internal_research: "Internal research", evaluation: "Evaluation", commercial: "Commercial" };
const fmt = (v: number | null | undefined, d = 3) => (v == null ? "—" : v.toFixed(d));

export default function ReleaseReview({ modelId, canApprove, onDone }: { modelId: number; canApprove: boolean; onDone: () => void }) {
  const [rv, setRv] = useState<RR | null>(null);
  const [scope, setScope] = useState("");
  const [reasons, setReasons] = useState<Record<string, string>>({});
  const [error, setError] = useState(""); const [busy, setBusy] = useState(false);
  useEffect(() => { api.releaseReview(modelId).then((r) => { setRv(r); setScope(r.allowed_scopes[0] ?? ""); }).catch((e) => setError(e.message)); }, [modelId]);
  if (!rv) return <p className="small muted">{error || "Loading release review…"}</p>;
  const needed = rv.issues.filter((i) => scope && i.needs_reason.includes(scope));
  const ready = !!scope && needed.every((i) => (reasons[i.id] ?? "").trim().length >= 20);
  const approve = async () => {
    setBusy(true); setError("");
    try { await api.approveModel(modelId, scope, reasons); onDone(); } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  };
  const t = rv.evidence.test as Record<string, number | null> & { ci95?: Record<string, [number, number]> } | null;
  return (
    <div className="release card">
      <h3>Release review</h3>
      <p className="small muted">Approving makes this the model every new detection job uses, for the scope you choose. Projects with a wider
        intended use are refused. Every open issue needs your written reason, kept in the audit trail.</p>
      {t && <p className="small"><b>Locked test sites{t.sites ? ` (${t.sites})` : ""}:</b> pixel F1 {fmt(t.f1)} ({fmt(t.ci95?.f1?.[0])}–{fmt(t.ci95?.f1?.[1])}),
        precision {fmt(t.precision)}, recall {fmt(t.recall)}, false-alarm area {fmt(t.false_alarm_km2_per_1000km2, 1)} km² per 1,000 km².</p>}
      {Object.entries(rv.evidence.paired).map(([b, m]) => <p key={b} className="small">
        <b>Paired against {b}</b> (test sites): {Object.entries(m).map(([k, v]) =>
          `${k.replace(/_/g, " ")} ${v.b_minus_a >= 0 ? "+" : ""}${v.b_minus_a.toFixed(3)} (${v.ci95[0].toFixed(3)} to ${v.ci95[1].toFixed(3)}, ${v.verdict})`).join(" · ")}</p>)}
      <fieldset className="scope"><legend className="small"><b>Approve for</b></legend>
        {rv.scopes.map((s) => { const blocked = !rv.allowed_scopes.includes(s); return (
          <label key={s} className={`small ${blocked ? "muted" : ""}`}><input type="radio" name={`scope-${modelId}`} disabled={blocked} checked={scope === s}
            onChange={() => setScope(s)} /> {SCOPE_LABEL[s]}{blocked && " (blocked: see issues)"}</label>); })}
      </fieldset>
      <ol className="issues">
        {rv.issues.map((i) => {
          const blocks = scope && i.blocks.includes(scope), needs = scope && i.needs_reason.includes(scope);
          return (
            <li key={i.id} className="small">
              <b>{i.title}</b> {i.blocks.length > 0 && <span className="badge warn">blocks {i.blocks.map((b) => SCOPE_LABEL[b].toLowerCase()).join(", ")}</span>}
              <div className="muted">{i.detail}</div>
              {needs && !blocks && canApprove && <textarea rows={2} aria-label={`Reason: ${i.title}`} placeholder="Why it is acceptable to proceed at this scope (at least 20 characters)"
                value={reasons[i.id] ?? ""} onChange={(e) => setReasons({ ...reasons, [i.id]: e.target.value })} />}
            </li>);
        })}
        {rv.issues.length === 0 && <li className="small">No open issues.</li>}
      </ol>
      {canApprove ? <button className="primary" disabled={busy || !ready} onClick={approve}>
        {busy ? "Approving…" : `Approve for ${scope ? SCOPE_LABEL[scope].toLowerCase() : "…"} use (second person)`}</button>
        : <p className="small muted">A different person from whoever registered or proposed this model approves it.</p>}
      {error && <p className="error small" role="alert">{error}</p>}
    </div>
  );
}
