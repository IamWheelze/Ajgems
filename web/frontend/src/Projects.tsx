import { FormEvent, useEffect, useState } from "react";
import { api, Me, Project } from "./api";

const TERMS = "I will use this project only to find visible land disturbance for review. Results do not establish " +
  "mineral type, ownership, legality or current activity, and will not be presented as if they do. Locations " +
  "marked restricted will not be shared.";

export default function Projects({ me }: { me: Me }) {
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [name, setName] = useState("");
  const [purpose, setPurpose] = useState("");
  const [use, setUse] = useState("internal_research");   // FR-025
  const [accepted, setAccepted] = useState(false);
  const [error, setError] = useState("");
  const canCreate = me.roles.includes("gis");

  const load = () => api.projects().then(setProjects).catch((e) => setError(e.message));
  useEffect(() => { load(); }, []);

  const create = async (e: FormEvent) => {
    e.preventDefault(); setError("");
    try {
      await api.createProject(name, purpose, use);
      setName(""); setPurpose(""); setAccepted(false); load();
    } catch (err) { setError((err as Error).message); }
  };

  return (
    <div className="page">
      <h1>Projects</h1>
      {error && <p className="error" role="alert">{error}</p>}
      {projects === null ? <p className="muted">Loading…</p> : projects.length === 0 ? (
        <p className="muted">No projects yet.</p>
      ) : (
        <div className="list">
          {projects.map((p) => (
            <div key={p.id} className="card row">
              <div className="grow">
                <h2>{p.name} {p.status === "archived" && <span className="badge">archived</span>}</h2>
                <p className="muted">{p.purpose} <span className="badge">{p.intended_use?.replace("_", " ")}</span></p>
                <p className="small muted">{p.areas} area{p.areas === 1 ? "" : "s"} · {p.detections} candidate footprints</p>
              </div>
              <div className="actions">
                <a className="button" href={`#/projects/${p.id}`}>Areas</a>
                <a className="button primary" href={`#/projects/${p.id}/review`}>Review</a>
              </div>
            </div>
          ))}
        </div>
      )}
      {canCreate && (
        <form className="card" onSubmit={create}>
          <h2>New project</h2>
          <label>Name<input value={name} onChange={(e) => setName(e.target.value)} /></label>
          <label>Purpose<textarea value={purpose} onChange={(e) => setPurpose(e.target.value)} rows={2}
            placeholder="What will this project be used for, and by whom?" /></label>
          <label>Intended use of the outputs
            <select value={use} onChange={(e) => setUse(e.target.value)}>
              <option value="internal_research">Internal research</option><option value="evaluation">Evaluation</option>
              <option value="commercial">Commercial</option>
            </select>
            <span className="small muted">Jobs use only imagery sources approved for this use. Planetary Computer's terms allow evaluation and testing only.</span>
          </label>
          <label className="check"><input type="checkbox" checked={accepted} onChange={(e) => setAccepted(e.target.checked)} />
            <span>{TERMS}</span></label>
          <button className="primary" disabled={!name.trim() || !purpose.trim() || !accepted}>Create project</button>
        </form>
      )}
    </div>
  );
}
