import { useEffect, useState } from "react";
import { api, Me } from "./api";
import Login from "./Login";
import Projects from "./Projects";
import ProjectAreas from "./ProjectAreas";
import ReviewWorkspace from "./ReviewWorkspace";
import JobMonitor from "./JobMonitor";
import MfaSetup from "./MfaSetup";
import Annotate from "./Annotate";
import ErrorMaps from "./ErrorMaps";
import Corrections from "./Corrections";
import Field from "./Field";
import Pool from "./Pool";
import RunDashboard from "./RunDashboard";
import Registry from "./Registry";
import DatasetExplorer from "./DatasetExplorer";

// Hash routes: #/projects, #/projects/:id, #/projects/:id/review, #/projects/:id/jobs/:jobId
function useRoute() {
  const [hash, setHash] = useState(window.location.hash || "#/projects");
  useEffect(() => {
    const on = () => setHash(window.location.hash || "#/projects");
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return hash.slice(1).split("/").filter(Boolean);
}

export default function App() {
  const [me, setMe] = useState<Me | null | undefined>(undefined);
  const route = useRoute();

  useEffect(() => {
    // Offline (FR-095) the CSRF request fails but the service worker still answers /api/me/ from its cache.
    api.csrf().catch(() => undefined).then(() => api.me()).then(setMe).catch(() => setMe(null));
  }, []);

  if (me === undefined) return <div className="centre muted">Loading…</div>;
  if (me === null) return <Login onSignedIn={setMe} />;

  const signOut = async () => { await api.logout().catch(() => undefined); setMe(null); };
  if (me.privileged && !me.mfa_satisfied) return <MfaSetup me={me} onDone={setMe} onSignOut={signOut} />;
  const [section, id, page, sub] = route;
  return (
    <div className="app">
      <header className="topbar">
        <a href="#/projects" className="brand">minefoot</a>
        <span className="muted">{me.organisation}</span>
        <a href="#/projects" className="small">Projects</a>
        {me.roles.some((r) => r === "ml" || r === "admin") && <a href="#/models" className="small">Models</a>}
        {me.roles.some((r) => r === "ml" || r === "admin") && <a href="#/datasets" className="small">Datasets</a>}
        {me.roles.some((r) => r === "ml" || r === "gis" || r === "admin") && <a href="#/pool" className="small">Annotation QC</a>}
        {me.roles.includes("field") && <a href="#/field" className="small">Field visits</a>}
        <a href="#/corrections" className="small">Corrections</a>
        <span className="spacer" />
        <span className="muted">{me.username} · {me.roles.join(", ")}</span>
        <button className="link" onClick={signOut}>Sign out</button>
      </header>
      <main className="content">
        {section === "models" && id === "dashboard" ? <RunDashboard />
          : section === "models" && id === "errors" && page ? <ErrorMaps modelId={Number(page)} />
          : section === "models" ? <Registry me={me} />
          : section === "datasets" ? <DatasetExplorer />
          : section === "pool" ? <Pool me={me} />
          : section === "annotate" && id ? <Annotate taskId={Number(id)} />
          : section === "field" ? <Field me={me} />
          : section === "corrections" ? <Corrections me={me} />
          : section === "projects" && id && page === "jobs" && sub ? <JobMonitor projectId={Number(id)} jobId={Number(sub)} me={me} />
          : section === "projects" && id && page === "review" ? <ReviewWorkspace projectId={Number(id)} me={me} />
          : section === "projects" && id ? <ProjectAreas projectId={Number(id)} me={me} />
          : <Projects me={me} />}
      </main>
    </div>
  );
}
