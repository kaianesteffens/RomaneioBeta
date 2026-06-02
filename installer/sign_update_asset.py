from __future__ import annotations

import base64
import os
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519


def _decode_b64(value: str, label: str) -> bytes:
    compact = "".join(str(value or "").strip().split())
    if not compact:
        raise ValueError(f"{label} nao definido.")
    try:
        return base64.b64decode(compact, validate=True)
    except Exception as exc:
        raise ValueError(f"{label} nao e base64 valido.") from exc


def _load_private_key(value: str) -> ed25519.Ed25519PrivateKey:
    if not value.strip():
        raise ValueError("UPDATE_SIGNING_PRIVATE_KEY_B64 nao definido.")

    raw_value = value.strip()
    if raw_value.startswith("-----BEGIN"):
        key = serialization.load_pem_private_key(raw_value.encode("utf-8"), password=None)
    else:
        key_bytes = _decode_b64(raw_value, "UPDATE_SIGNING_PRIVATE_KEY_B64")
        if len(key_bytes) == 32:
            return ed25519.Ed25519PrivateKey.from_private_bytes(key_bytes)
        key = serialization.load_der_private_key(key_bytes, password=None)

    if not isinstance(key, ed25519.Ed25519PrivateKey):
        raise ValueError("A chave privada de update deve ser Ed25519.")
    return key


def _load_public_key(value: str) -> ed25519.Ed25519PublicKey:
    if not value.strip():
        raise ValueError("UPDATE_PUBLIC_KEY_B64 nao definido.")

    key_bytes = _decode_b64(value, "UPDATE_PUBLIC_KEY_B64")
    if len(key_bytes) == 32:
        return ed25519.Ed25519PublicKey.from_public_bytes(key_bytes)

    key = serialization.load_der_public_key(key_bytes)
    if not isinstance(key, ed25519.Ed25519PublicKey):
        raise ValueError("A chave publica de update deve ser Ed25519.")
    return key


def _verify_with_public_key(zip_bytes: bytes, signature: bytes) -> None:
    public_key_b64 = os.environ.get("UPDATE_PUBLIC_KEY_B64", "")
    if not public_key_b64.strip():
        print("AVISO: UPDATE_PUBLIC_KEY_B64 nao definido; assinatura gerada sem verificacao cruzada.", file=sys.stderr)
        return

    public_key = _load_public_key(public_key_b64)
    try:
        public_key.verify(signature, zip_bytes)
    except Exception as exc:
        raise ValueError(
            "Assinatura gerada nao valida com UPDATE_PUBLIC_KEY_B64. "
            "Confira se UPDATE_SIGNING_PRIVATE_KEY_B64 e UPDATE_PUBLIC_KEY_B64 pertencem ao mesmo par Ed25519."
        ) from exc


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("Uso: python installer/sign_update_asset.py <zip> <saida.sig>", file=sys.stderr)
        return 2

    zip_path = Path(argv[1])
    sig_path = Path(argv[2])
    if not zip_path.exists():
        print(f"ZIP nao encontrado: {zip_path}", file=sys.stderr)
        return 2

    private_key = _load_private_key(os.environ.get("UPDATE_SIGNING_PRIVATE_KEY_B64", ""))
    zip_bytes = zip_path.read_bytes()
    signature = private_key.sign(zip_bytes)
    _verify_with_public_key(zip_bytes, signature)
    sig_path.write_text(base64.b64encode(signature).decode("ascii") + "\n", encoding="ascii")
    print(f"Assinatura gerada e verificada: {sig_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))