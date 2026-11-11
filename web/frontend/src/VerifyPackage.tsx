import { useEffect, useState } from "react";
import { api } from "./api";

// FR-109: show the signing key fingerprint, and check a package someone sends back.
export default function VerifyPackage() {
  const [key, setKey] = useState<{ key_id: string; public_key: string; development_key: boolean } | null>(null);
  const [result, setResult] = useState<{ ok: boolean; problems: string[]; files_checked?: number } | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => { api.signingKey().then(setKey).catch(() => setKey(null)); }, []);
  const check = async (f: File) => {
    setBusy(true); setResult(null);
    try { setResult(await api.verifyExport(f)); } catch (e) { setResult({ ok: false, problems: [(e as Error).message] }); }
    finally { setBusy(false); }
  };
  return (
    <details className="small">
      <summary>Signed packages: verify one</summary>
      {key && <p>Packages are signed with Ed25519, key ID <code>{key.key_id}</code>{key.development_key && <span className="badge warn"> development key: not for real deliveries</span>}.
        Public key for recipients: <code className="wrap-anywhere">{key.public_key}</code>. Each package also contains
        <code> verify_export.py</code> to check it offline.</p>}
      <input type="file" accept=".zip" disabled={busy} onChange={(e) => e.target.files?.[0] && check(e.target.files[0])} />
      {result && (result.ok ? <p className="badge">Verified: signature valid and all {result.files_checked} files unchanged</p>
        : <div className="error">Not verified:<ul>{result.problems.map((p) => <li key={p}>{p}</li>)}</ul></div>)}
    </details>
  );
}
