import { useEffect, useState } from "react";
import { api, ApiError, Me } from "./api";
import Login from "./Login";
import Projects from "./Projects";
import ProjectAreas from "./ProjectAreas";
import ReviewWorkspace from "./ReviewWorkspace";

// Hash routes: #/projects, #/projects/:id/areas, #/projects/:id/review
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
    api.csrf().then(() => api.me()).then(setMe).catch((e) => {
      if (e instanceof ApiError && (e.status === 401 || e.status === 403)) setMe(null);
      else setMe(null);
    });
  }, []);

  if (me === undefined) return <div className="centre muted">Loading…</div>;
  if (me === null) return <Login onSignedIn={setMe} />;

  const signOut = async () => { await api.logout(); setMe(null); };
  const [section, id, page] = route;
  return (
    <div className="app">
      <header className="topbar">
        <a href="#/projects" className="brand">minefoot</a>
        <span className="muted">{me.organisation}</span>
        <span className="spacer" />
        {me.privileged && !me.mfa_enabled && (
          <span className="badge warn" title="FR-001: multi-factor sign-in is required for privileged roles and is not built yet">
            Multi-factor sign-in not set up
          </span>
        )}
        <span className="muted">{me.username} · {me.roles.join(", ")}</span>
        <button className="link" onClick={signOut}>Sign out</button>
      </header>
      <main className="content">
        {section === "projects" && id && page === "review" ? <ReviewWorkspace projectId={Number(id)} me={me} />
          : section === "projects" && id ? <ProjectAreas projectId={Number(id)} me={me} />
          : <Projects me={me} />}
      </main>
    </div>
  );
}
