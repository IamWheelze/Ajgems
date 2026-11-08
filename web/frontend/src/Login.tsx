import { FormEvent, useState } from "react";
import { api, Me } from "./api";

export default function Login({ onSignedIn }: { onSignedIn: (m: Me) => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true); setError("");
    try {
      await api.csrf();
      onSignedIn(await api.login(username, password));
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="centre">
      <form className="card narrow" onSubmit={submit}>
        <h1>minefoot</h1>
        <p className="muted">Candidate mining-footprint review. Sign in with your organisation account.</p>
        <label>Username<input value={username} onChange={(e) => setUsername(e.target.value)} autoFocus autoComplete="username" /></label>
        <label>Password<input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" /></label>
        {error && <p className="error" role="alert">{error}</p>}
        <button className="primary" disabled={busy || !username || !password}>{busy ? "Signing in…" : "Sign in"}</button>
      </form>
    </div>
  );
}
