"""Export signing (FR-109): Ed25519 over the canonical manifest, which lists every file's SHA-256.

The private key comes from MINEFOOT_SIGNING_KEY (base64 of a 32-byte seed). Generate one with
`python -c "import os,base64;print(base64.b64encode(os.urandom(32)).decode())"` and keep it secret.
Without it, a development key derived from DJANGO_SECRET_KEY is used and every signature says so.
"""
import base64
import hashlib
import os
from pathlib import Path

from django.conf import settings

from .verify_export import canonical, key_id, verify  # noqa: F401  (verify is re-exported for views and tests)


def _private_key():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    seed = os.environ.get("MINEFOOT_SIGNING_KEY")
    if seed:
        return Ed25519PrivateKey.from_private_bytes(base64.b64decode(seed)), False
    return Ed25519PrivateKey.from_private_bytes(hashlib.sha256(("export-signing:" + settings.SECRET_KEY).encode()).digest()), True


def public_key():
    return _private_key()[0].public_key()


def public_info() -> dict:
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    pk, dev = public_key(), _private_key()[1]
    return {"algorithm": "Ed25519", "key_id": key_id(pk), "development_key": dev,
            "public_key": base64.b64encode(pk.public_bytes(Encoding.Raw, PublicFormat.Raw)).decode(),
            "pem": pk.public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo).decode()}


def sign(manifest: dict) -> dict:
    key, dev = _private_key()
    pk = key.public_key()
    manifest = {k: v for k, v in manifest.items() if k != "signature"}
    manifest["signature"] = {"algorithm": "Ed25519", "key_id": key_id(pk), "development_key": dev,
                             "value": base64.b64encode(key.sign(canonical(manifest))).decode(),
                             "covers": "every manifest field except 'signature', as JSON with sorted keys and no spaces"}
    return manifest


VERIFIER = Path(__file__).with_name("verify_export.py")


def how_to_verify(info: dict) -> str:
    return (f"This package is signed (FR-109). Signing key ID: {info['key_id']}.\n\n"
            "To verify, get the organisation's public key from the minefoot server (Settings > Signing key, or "
            "/api/signing-key/), not from this package, and run:\n\n"
            "    pip install cryptography\n    python verify_export.py <this zip> --public-key <key>\n\n"
            "VERIFIED means the manifest is signed by that key and every file matches its SHA-256. Any edited, "
            "missing or added file fails.\n"
            + ("\nWARNING: signed with a development key. Not valid for real deliveries.\n" if info["development_key"] else ""))
