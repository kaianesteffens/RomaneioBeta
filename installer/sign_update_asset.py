from __future__ import annotations

import base64
import os
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519


def _load_private_key(value: str) -> ed25519.Ed25519PrivateKey:
    if not value.strip():
        raise ValueError("UPDATE_SIGNING_PRIVATE_KEY_B64 nao definido.")

    raw_value = value.strip()
    if raw_value.startswith("-----BEGIN"):
        key = serialization.load_pem_private_key(raw_value.encode("utf-8"), password=None)
    else:
        key_bytes = base64.b64decode("".join(raw_value.split()), validate=True)
        if len(key_bytes) == 32:
            return ed25519.Ed25519PrivateKey.from_private_bytes(key_bytes)
        key = serialization.load_der_private_key(key_bytes, password=None)

    if not isinstance(key, ed25519.Ed25519PrivateKey):
        raise ValueError("A chave privada de update deve ser Ed25519.")
    return key


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
    signature = private_key.sign(zip_path.read_bytes())
    sig_path.write_text(base64.b64encode(signature).decode("ascii") + "\n", encoding="ascii")
    print(f"Assinatura gerada: {sig_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
