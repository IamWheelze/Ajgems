import { FormEvent, useState } from "react";
import { api, Me } from "./api";

// Password first; users with multi-factor sign-in then enter their 6-digit code (FR-001).
export default function Login({ onSignedIn }: { onSignedIn: (m: Me) => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [needCode, setNeedCode] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true); setError("");
    try {
      await api.csrf();
      if (needCode) { onSignedIn(await api.mfaVerify(code)); return; }
      const r = await api.login(username, password);
      if ("mfa_required" in r) { setNeedCode(true); await api.csrf(); }
      else onSignedIn(r);
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
        {!needCode ? (
          <>
            <label>Username<input value={username} onChange={(e) => setUsername(e.target.value)} autoFocus autoComplete="username" /></label>
            <label>Password<input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" /></label>
          </>
        ) : (
          <label>Code from your authenticator app
            <input inputMode="numeric" pattern="[0-9]*" maxLength={6} value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
              autoFocus autoComplete="one-time-code" />
          </label>
        )}
        {error && <p className="error" role="alert">{error}</p>}
        <button className="primary" disabled={busy || (needCode ? code.length !== 6 : !username || !password)}>
          {busy ? "Signing in…" : needCode ? "Verify code" : "Sign in"}
        </button>
      </form>
    </div>
  );
}
