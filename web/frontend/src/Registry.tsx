import ReleaseReview from "./ReleaseReview";
import { useEffect, useState } from "react";
import { api, Me, ModelVersion } from "./api";
import { human } from "./status";

// Model registry (FR-101 to FR-104): lineage, comparison on locked sites, two-person approval, rollback.
const fmt = (v: number | null | undefined, d = 3) => (v === null || v === undefined ? "—" : v.toFixed(d));

export default function Registry({ me }: { me: Me }) {
  const [models, setModels] = useState<ModelVersion[] | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState<number | null>(null);
  const load = () => api.models().then(setModels).catch((e) => setError(e.message));
  useEffect(() => { load(); }, []);
  const ml = me.roles.includes("ml"), admin = me.roles.includes("admin");

  const act = async (m: ModelVersion, action: string) => {
    setBusy(m.id); setError("");
    try { await api.modelAction(m.id, action); await load(); } catch (e) { setError(`${m.name}: ${(e as Error).message}`); }
    finally { setBusy(null); }
  };

  return (
    <div className="page">
      <h1>Model registry</h1>
      <p className="small muted">Detection jobs use the production model. Promotion needs a comparison on the same locked test
        sites (FR-103) and approval by a second person (FR-104). Register runs with <code>python manage.py register_model</code>. <a href="#/models/dashboard">Run dashboard →</a></p>
      {error && <p className="error" role="alert">{error}</p>}
      {models === null ? <p className="muted">Loading…</p> : models.length === 0 ? <p className="muted">No models registered.</p> : models.map((m) => (
        <div key={m.id} className="card">
          <div className="row gap">
            <h2 className="grow">{m.name}</h2>
            <span className={`badge ${m.state === "production" ? "" : m.state === "rolled_back" ? "warn" : ""}`}>{human(m.state)}</span>
            <a className="small" href={`#/models/errors/${m.id}`}>Error maps →</a>
          </div>
          <dl className="facts small">
            <dt>Commit</dt><dd><code>{m.code_commit.slice(0, 10)}</code></dd>
            <dt>Dataset</dt><dd>{m.dataset.name} {m.dataset.version} ({m.dataset.licence})</dd>
            <dt>Input schema</dt><dd>{m.input_schema}</dd>
            <dt>Threshold</dt><dd>{fmt(m.threshold, 2)} (model score)</dd>
            <dt>Validation</dt><dd>F1 {fmt(m.metrics.val?.f1)} · precision {fmt(m.metrics.val?.precision)} · recall {fmt(m.metrics.val?.recall)}{" "}
              · false alarm {fmt(m.metrics.val?.false_alarm_km2_per_1000km2, 1)} km² per 1,000 km²</dd>
            <dt>Test</dt><dd>F1 {fmt(m.metrics.test?.f1)}</dd>
            <dt>Domain</dt><dd>{String(m.card.validated_domain)}</dd>
            <dt>Licences</dt><dd>{m.licences.map((l) => `${l.item}: ${l.licence}`).join(" · ")}</dd>
            <dt>People</dt><dd>registered by {m.registered_by}{m.proposed_by && ` · proposed by ${m.proposed_by}`}{m.approved_by && ` · approved by ${m.approved_by}`}</dd>
          </dl>
          {m.reports.map((r) => (
            <details key={r.id} className="small"><summary>Comparison on {r.split} ({r.sites} sites) against {r.baseline ?? "no production model"}</summary>
              {r.result.note ? <p>{r.result.note}</p> : (
                <div className="scroll"><table>
                  <thead><tr><th>Metric</th><th>Production</th><th>This model</th><th>Difference (95% CI)</th><th>Verdict</th></tr></thead>
                  <tbody>{Object.entries(r.result.metrics ?? {}).map(([k, v]) => (
                    <tr key={k}><td>{human(k)}</td><td>{fmt(v.a)}</td><td>{fmt(v.b)}</td>
                      <td>{fmt(v.b_minus_a)} ({fmt(v.ci95[0])} to {fmt(v.ci95[1])})</td><td>{v.verdict}</td></tr>))}</tbody>
                </table></div>
              )}
            </details>
          ))}
          {m.state === "candidate" && (ml || admin) && <ReleaseReview modelId={m.id} canApprove={me.username !== m.proposed_by && me.username !== m.registered_by} onDone={load} />}
          <div className="actions wrap">
            {ml && ["experimental", "under_evaluation"].includes(m.state) && <button disabled={busy === m.id} onClick={() => act(m, "compare")}>Compare with production</button>}
            {ml && m.state === "under_evaluation" && <button disabled={busy === m.id} onClick={() => act(m, "propose")}>Propose for production</button>}
            {m.approved_scope && <span className="badge">Approved for {m.approved_scope.replace(/_/g, " ")} by {m.approved_by}</span>}
            {(ml || admin) && m.state === "production" && m.previous_production &&
              <button disabled={busy === m.id} onClick={() => act(m, "rollback")}>Roll back to {m.previous_production}</button>}
          </div>
        </div>
      ))}
    </div>
  );
}
