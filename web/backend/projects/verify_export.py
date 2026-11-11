#!/usr/bin/env python3
"""Verify a minefoot export package (FR-109). Standalone: needs Python 3.9+ and the `cryptography` package.

    python verify_export.py package.zip --public-key KEY

KEY is the organisation's Ed25519 public key (base64 of the 32 raw bytes, or a PEM file path). Get it from the
minefoot server (/api/signing-key/) or from your supplier, never from the package itself: a key shipped inside the
package proves nothing. The check passes only if the manifest's signature is valid for that key, every file's
SHA-256 and size match the manifest, and no file is missing or added. Exit code 0 means verified.
"""
import argparse
import base64
import hashlib
import json
import sys
import zipfile
from pathlib import Path

UNSIGNED = {"manifest.json", "verify_export.py", "HOW_TO_VERIFY.txt"}   # outside the signed file list by design


def canonical(manifest: dict) -> bytes:
    body = {k: v for k, v in manifest.items() if k != "signature"}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def load_public_key(text: str):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    from cryptography.hazmat.primitives.serialization import load_pem_public_key
    p = Path(text)
    if p.exists():
        text = p.read_text()
    text = text.strip()
    if text.startswith("-----BEGIN"):
        return load_pem_public_key(text.encode())
    return Ed25519PublicKey.from_public_bytes(base64.b64decode(text))


def key_id(public_key) -> str:
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    return hashlib.sha256(public_key.public_bytes(Encoding.Raw, PublicFormat.Raw)).hexdigest()[:16]


def verify(zip_bytes: bytes, public_key) -> dict:
    from cryptography.exceptions import InvalidSignature
    problems = []
    try:
        z = zipfile.ZipFile(__import__("io").BytesIO(zip_bytes))
        manifest = json.loads(z.read("manifest.json"))
    except (zipfile.BadZipFile, KeyError, ValueError) as exc:
        return {"ok": False, "problems": [f"not a readable export package: {exc}"]}
    sig = manifest.get("signature") or {}
    if sig.get("algorithm") != "Ed25519" or not sig.get("value"):
        problems.append("the manifest is not signed")
    else:
        if sig.get("key_id") != key_id(public_key):
            problems.append(f"signed with key {sig.get('key_id')}, not the key given ({key_id(public_key)})")
        try:
            public_key.verify(base64.b64decode(sig["value"]), canonical(manifest))
        except InvalidSignature:
            problems.append("signature does not match the manifest: the manifest was altered or signed with another key")
    listed = manifest.get("files", {})
    names = set(z.namelist())
    for name, info in listed.items():
        if name not in names:
            problems.append(f"missing file: {name}")
            continue
        data = z.read(name)
        if hashlib.sha256(data).hexdigest() != info.get("sha256") or len(data) != info.get("bytes"):
            problems.append(f"altered file: {name}")
    for extra in sorted(names - set(listed) - UNSIGNED):
        problems.append(f"file not in the manifest: {extra}")
    return {"ok": not problems, "problems": problems, "key_id": sig.get("key_id"), "files_checked": len(listed),
            "development_key": bool(sig.get("development_key"))}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("package"); ap.add_argument("--public-key", required=True)
    a = ap.parse_args(argv)
    r = verify(Path(a.package).read_bytes(), load_public_key(a.public_key))
    print("VERIFIED" if r["ok"] else "NOT VERIFIED")
    for p in r["problems"]:
        print(" -", p)
    if r.get("development_key"):
        print(" (signed with a development key: not valid for real deliveries)")
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
