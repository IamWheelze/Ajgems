import { FormEvent, useEffect, useState } from "react";
import { api, Me } from "./api";

// Enrolment for privileged roles (FR-001): nothing else is reachable until this is done.
export default function MfaSetup({ me, onDone, onSignOut }: { me: Me; onDone: (m: Me) => void; onSignOut: () => void }) {
  const [setup, setSetup] = useState<{ secret: string; qr_png: string } | null>(null);
  const [code, setCode] = useState("");
  const [error, setError] = useState("");
  useEffect(() => { api.mfaSetup().then(setSetup).catch((e) => setError(e.message)); }, []);

  const confirm = async (e: FormEvent) => {
    e.preventDefault(); setError("");
    try { onDone(await api.mfaConfirm(code)); } catch (err) { setError((err as Error).message); }
  };

  return (
    <div className="centre">
      <form className="card narrow" onSubmit={confirm}>
        <h1>Set up multi-factor sign-in</h1>
        <p className="small">Your role ({me.roles.join(", ")}) needs a second factor. Scan the code with an authenticator app
          (for example Google Authenticator, Microsoft Authenticator or 1Password), then enter the 6-digit code it shows.</p>
        {setup ? (
          <>
            <img src={setup.qr_png} alt="QR code for your authenticator app" width={200} height={200} style={{ justifySelf: "center", background: "#fff" }} />
            <p className="small muted">Can't scan? Enter this key: <code>{setup.secret.replace(/(.{4})/g, "$1 ").trim()}</code></p>
          </>
        ) : !error && <p className="muted">Preparing…</p>}
        <label>Code<input inputMode="numeric" maxLength={6} value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))} autoComplete="one-time-code" /></label>
        {error && <p className="error" role="alert">{error}</p>}
        <button className="primary" disabled={code.length !== 6}>Confirm and continue</button>
        <button type="button" className="link" onClick={onSignOut}>Sign out</button>
      </form>
    </div>
  );
}
