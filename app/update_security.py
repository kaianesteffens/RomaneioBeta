"""Verificacao de assinatura dos pacotes de update do Fretio."""
from __future__ import annotations

import base64
import os
import sys
from pathlib import Path
from typing import Any


_CONFIG_SECTIONS = ("fretio", "fretebot", "romaneio")
_UPDATE_PUBLIC_KEY_ENV_VARS = (
    "FRETIO_UPDATE_PUBLIC_KEY_B64",
    "FRETEBOT_UPDATE_PUBLIC_KEY_B64",
    "Fretio_UPDATE_PUBLIC_KEY_B64",
)
_EMBEDDED_UPDATE_PUBLIC_KEY_B64 = ""


class UpdateSignatureError(ValueError):
    """Erro ao verificar assinatura do update."""


def _load_toml_file(path: Path) -> dict[str, Any]:
    raw = path.read_text(encoding="utf-8-sig")
    data = None
    try:
        import tomllib  # type: ignore[import]
        data = tomllib.loads(raw)
    except ImportError:
        pass
    if data is None:
        try:
            import toml  # type: ignore[import-untyped]
            data = toml.loads(raw)
        except ImportError:
            pass
    if data is None:
        import tomli  # type: ignore[import-not-found]
        data = tomli.loads(raw)
    return data if isinstance(data, dict) else {}


def _iter_config_paths() -> list[Path]:
    paths: list[Path] = []
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    paths.append(base / "CONFIG.toml")
    if base != Path(__file__).parent:
        paths.append(Path(__file__).parent / "CONFIG.toml")
    return paths


def _get_config_public_key() -> str:
    try:
        for path in _iter_config_paths():
            if not path.exists():
                continue
            config = _load_toml_file(path)
            for section_name in _CONFIG_SECTIONS:
                section = config.get(section_name, {})
                if not isinstance(section, dict):
                    continue
                value = section.get("update_public_key_b64", "")
                if value:
                    return str(value).strip()
    except Exception:
        pass
    return ""


def _get_update_public_key_b64() -> str:
    for env_name in _UPDATE_PUBLIC_KEY_ENV_VARS:
        value = os.getenv(env_name, "").strip()
        if value:
            return value
    config_value = _get_config_public_key()
    if config_value:
        return config_value
    return _EMBEDDED_UPDATE_PUBLIC_KEY_B64


def _decode_base64(value: str, label: str) -> bytes:
    compact = "".join(str(value or "").strip().split())
    if not compact:
        raise UpdateSignatureError(f"{label} ausente.")
    try:
        return base64.b64decode(compact, validate=True)
    except Exception as exc:
        raise UpdateSignatureError(f"{label} em formato invalido.") from exc


def _load_public_key(public_key_b64: str):
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ed25519
    except ImportError as exc:
        raise UpdateSignatureError("Verificacao de assinatura indisponivel.") from exc

    key_bytes = _decode_base64(public_key_b64, "Chave publica de update")
    try:
        if len(key_bytes) == 32:
            return ed25519.Ed25519PublicKey.from_public_bytes(key_bytes)
        return serialization.load_der_public_key(key_bytes)
    except Exception as exc:
        raise UpdateSignatureError("Chave publica de update invalida.") from exc


def _load_signature(signature_path: Path) -> bytes:
    if not signature_path.exists():
        raise UpdateSignatureError("Assinatura do update ausente.")

    raw = signature_path.read_bytes()
    try:
        text = raw.decode("ascii").strip()
    except UnicodeDecodeError:
        text = ""

    if text:
        try:
            decoded = base64.b64decode("".join(text.split()), validate=True)
            if len(decoded) == 64:
                return decoded
        except Exception:
            pass

    if len(raw) == 64:
        return raw
    raise UpdateSignatureError("Assinatura do update em formato invalido.")


def verify_update_signature(
    zip_path: Path,
    signature_path: Path,
    public_key_b64: str | None = None,
) -> None:
    """Valida assinatura Ed25519 do ZIP de update.

    Levanta UpdateSignatureError quando a assinatura nao pode ser verificada.
    """
    public_key = _load_public_key(public_key_b64 or _get_update_public_key_b64())
    signature = _load_signature(signature_path)
    payload = zip_path.read_bytes()
    try:
        public_key.verify(signature, payload)
    except Exception as exc:
        raise UpdateSignatureError("Assinatura do update invalida.") from exc
