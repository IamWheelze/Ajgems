import { useEffect, useState } from "react";
import { api } from "./api";

// FR-005: administrators grant named users explicit permission to see and export this project's restricted sites.
export default function RestrictedGrants({ projectId }: { projectId: number }) {
  const [grants, setGrants] = useState<Awaited<ReturnType<typeof api.restrictedGrants>>>([]);
  const [user, setUser] = useState(""); const [reason, setReason] = useState(""); const [error, setError] = useState("");
  const load = () => api.restrictedGrants(projectId).then(setGrants).catch((e) => setError(e.message));
  useEffect(() => { load(); }, [projectId]);
  const act = (u: string, r: string, revoke = false) => api.grantRestricted(projectId, u, r, revoke)
    .then(() => { setUser(""); setReason(""); setError(""); load(); }).catch((e) => setError(e.message));
  const active = grants.filter((g) => !g.revoked_at);
  return (
    <div className="card small">
      <h3>Restricted sites: access</h3>
      <p className="muted">Restricted locations are hidden from viewers and left out of exports unless a user is granted access here.</p>
      {active.length === 0 ? <span className="muted">No one has access.</span> : (
        <ul className="plain">{active.map((g) => <li key={g.id}><b>{g.user}</b> · {g.reason} · by {g.granted_by}{" "}
          <button className="link" onClick={() => { const r = prompt(`Reason for revoking ${g.user}'s access?`); if (r) act(g.user, r, true); }}>Revoke</button></li>)}</ul>
      )}
      <label>Username<input value={user} onChange={(e) => setUser(e.target.value)} /></label>
      <label>Reason<input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="e.g. signed data-sharing agreement" /></label>
      <button disabled={!user.trim() || !reason.trim()} onClick={() => act(user.trim(), reason.trim())}>Grant access</button>
      {error && <p className="error">{error}</p>}
    </div>
  );
}
