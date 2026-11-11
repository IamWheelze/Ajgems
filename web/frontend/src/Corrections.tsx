import { useEffect, useState } from "react";
import { api, Correction, Me } from "./api";
import { human } from "./status";

// FR-096: correction requests on released results, each with an owner, a state and a written outcome.
export default function Corrections({ me }: { me: Me }) {
  const [list, setList] = useState<Correction[]>([]);
  const [error, setError] = useState("");
  const [form, setForm] = useState<Record<number, { owner?: string; outcome?: string; apply?: string; reason?: string }>>({});
  const load = () => api.corrections().then(setList).catch((e) => setError(e.message));
  useEffect(() => { load(); }, []);
  const act = (id: number, action: string, body: Record<string, unknown>) =>
    api.correctionAction(id, action, body).then(() => { setError(""); load(); }).catch((e) => setError(e.message));
  const admin = me.roles.includes("admin");
  return (
    <div className="page">
      <h1>Correction requests</h1>
      <p className="small muted">Requests to correct released results. An administrator assigns an owner, who accepts or declines with a written
        outcome. An accepted correction is applied through the normal review rules, and every step is in the audit trail.</p>
      {error && <p className="error" role="alert">{error}</p>}
      {list.length === 0 && <p className="muted">No requests.</p>}
      {list.map((r) => {
        const f = form[r.id] ?? {}; const set = (k: string, v: string) => setForm({ ...form, [r.id]: { ...f, [k]: v } });
        const canResolve = r.state === "in_review" && (r.owner === me.username || admin);
        return (
          <div key={r.id} className="card small">
            <div className="row gap"><b className="grow">#{r.id} · candidate #{r.detection} · {human(r.kind)}</b>
              <span className={`badge ${r.state === "open" || r.state === "in_review" ? "warn" : ""}`}>{human(r.state)}</span></div>
            <span>{r.description}</span>
            <span className="muted">From {r.requested_by}{r.contact && ` (${r.contact})`} · owner {r.owner ?? "none yet"}</span>
            {r.outcome && <span><b>Outcome:</b> {r.outcome}</span>}
            <ol className="history">{r.events.map((e, i) => <li key={i}>{e.at.slice(0, 16).replace("T", " ")} · {e.actor}: {human(e.action)}{e.note && ` (${e.note})`}</li>)}</ol>
            {admin && ["open", "in_review"].includes(r.state) && <div className="row gap">
              <input placeholder="owner username (GIS or admin)" value={f.owner ?? ""} onChange={(e) => set("owner", e.target.value)} />
              <button onClick={() => act(r.id, "assign", { owner: f.owner })} disabled={!f.owner}>Assign owner</button></div>}
            {canResolve && <div className="decide">
              <label>Outcome for the requester<textarea rows={2} value={f.outcome ?? ""} onChange={(e) => set("outcome", e.target.value)} /></label>
              <label>Apply when accepting<select value={f.apply ?? ""} onChange={(e) => set("apply", e.target.value)}>
                <option value="">Nothing (explanation only)</option><option value="reject">Reject the candidate</option>
                <option value="restrict">Restrict the location</option></select></label>
              {f.apply === "reject" && <label>Reject reason<select value={f.reason ?? ""} onChange={(e) => set("reason", e.target.value)}><option value="">Choose…</option>
                {["farmland", "settlement_or_construction", "riverbank_or_water", "bare_natural_ground", "quarry_or_borrow_pit", "burn_scar", "other"].map((x) =>
                  <option key={x} value={x}>{human(x)}</option>)}</select></label>}
              <div className="actions">
                <button className="primary" disabled={!f.outcome?.trim() || (f.apply === "reject" && !f.reason)} onClick={() => act(r.id, "resolve", {
                  decision: "accept", outcome: f.outcome, apply: f.apply ? { action: f.apply, reason_code: f.reason } : undefined })}>Accept</button>
                <button disabled={!f.outcome?.trim()} onClick={() => act(r.id, "resolve", { decision: "decline", outcome: f.outcome })}>Decline</button>
              </div></div>}
            {r.requested_by === me.username && ["open", "in_review"].includes(r.state) &&
              <button className="link" onClick={() => act(r.id, "withdraw", {})}>Withdraw my request</button>}
          </div>
        );
      })}
    </div>
  );
}
