"""
Fretio — Sistema de Licenciamento.

Valida licenças contra um JSON remoto (GitHub Gist secreto).
Cada instalação precisa de uma chave para funcionar.
O administrador pode revogar licenças remotamente.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional
from urllib.error import URLError
from urllib.request import Request, urlopen

# ── Configuração ────────────────────────────────────────────────
_HTTP_TIMEOUT = 15
_GRACE_DAYS = 7  # dias de funcionamento offline após última validação
_CONFIG_SECTIONS = ("fretio", "fretebot", "romaneio")


def _load_toml_file(path: Path) -> dict:
    """Carrega TOML aceitando UTF-8 com/sem BOM."""
    raw = path.read_text(encoding="utf-8-sig")
    try:
        import toml  # type: ignore[import-untyped]
        data = toml.loads(raw)
    except ImportError:
        import tomli  # type: ignore[import-not-found]
        data = tomli.loads(raw)
    return data if isinstance(data, dict) else {}

# Onde salvar dados de licença localmente
def _license_dir() -> Path:
    appdata = os.getenv("APPDATA")
    if appdata:
        d = Path(appdata) / "Fretio"
    else:
        d = Path.home() / ".Fretio"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _license_file() -> Path:
    return _license_dir() / "license.key"


def _validation_cache_file() -> Path:
    return _license_dir() / ".license_cache"


# ── ID de Máquina ───────────────────────────────────────────────
def get_machine_id() -> str:
    """
    Gera um fingerprint único do hardware (Windows).
    Usa UUID da BIOS + serial do disco C.
    """
    parts: list[str] = []

    # UUID da BIOS via WMIC
    try:
        result = subprocess.run(
            ["wmic", "csproduct", "get", "UUID"],
            capture_output=True, text=True, timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        for line in result.stdout.strip().splitlines():
            line = line.strip()
            if line and line.upper() != "UUID":
                parts.append(line)
                break
    except Exception:
        pass

    # Serial do volume C:
    try:
        result = subprocess.run(
            ["vol", "C:"],
            capture_output=True, text=True, timeout=10,
            shell=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        for line in result.stdout.strip().splitlines():
            if "-" in line:
                # Pega o serial (ex: "1234-ABCD")
                serial = line.strip().split()[-1]
                parts.append(serial)
                break
    except Exception:
        pass

    # Fallback: hostname + user
    if not parts:
        parts.append(platform.node())
        parts.append(os.getenv("USERNAME", "unknown"))

    raw = "|".join(parts)
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


# ── Chave de Licença Local ──────────────────────────────────────
def get_saved_license() -> str:
    """Lê a chave de licença salva localmente. Retorna '' se não existir."""
    f = _license_file()
    if f.exists():
        return f.read_text(encoding="utf-8").strip()
    return ""


def save_license(key: str) -> None:
    """Salva a chave de licença localmente."""
    _license_file().write_text(key.strip().upper(), encoding="utf-8")


def remove_license() -> None:
    """Remove a licença salva."""
    f = _license_file()
    if f.exists():
        f.unlink()
    c = _validation_cache_file()
    if c.exists():
        c.unlink()


# ── Validação Remota ────────────────────────────────────────────
@dataclass
class LicenseStatus:
    valid: bool
    owner: str = ""
    message: str = ""
    blocked: bool = False
    expires: str = ""  # ISO date ou vazio
    offline: bool = False  # True se validou via cache local


def _iter_config_paths() -> list[Path]:
    config_paths: list[Path] = []
    appdata = os.getenv("APPDATA")
    if appdata:
        config_paths.append(Path(appdata) / "Fretio" / "CONFIG.toml")
        config_paths.append(Path(appdata) / "FreteBot" / "CONFIG.toml")
    base = Path(getattr(sys, '_MEIPASS', Path(__file__).parent))
    config_paths.append(base / "CONFIG.toml")
    if base != Path(__file__).parent:
        config_paths.append(Path(__file__).parent / "CONFIG.toml")
    return config_paths


def _get_config_value(key: str) -> str:
    try:
        for cp in _iter_config_paths():
            if not cp.exists():
                continue
            cfg = _load_toml_file(cp)
            for section_name in _CONFIG_SECTIONS:
                section = cfg.get(section_name, {})
                if not isinstance(section, dict):
                    continue
                value = section.get(key, "")
                if value:
                    return str(value).strip()
    except Exception:
        pass
    return ""


def _get_license_api_url() -> str:
    """Lê endpoint próprio de licenciamento, sem usar token GitHub."""
    for env_name in ("FRETIO_LICENSE_API_URL", "FRETEBOT_LICENSE_API_URL", "Fretio_LICENSE_API_URL"):
        url = os.environ.get(env_name, "").strip()
        if url:
            return url
    return _get_config_value("license_api_url")


def _get_gist_url() -> str:
    """Lê a URL do gist de licenças do CONFIG.toml ou variável de ambiente."""
    url = os.environ.get("Fretio_LICENSE_URL", "").strip()
    if url:
        return url

    return _get_config_value("license_url")


def _fetch_licenses(gist_url: str) -> dict:
    """Busca o JSON de licenças do gist remoto."""
    req = Request(gist_url, headers={
        "Accept": "application/json",
        "User-Agent": "Fretio-License/1.0",
    })
    with urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
        return json.loads(resp.read())


def _fetch_licenses_fresh() -> dict:
    """Busca licenças via API do GitHub (sem cache CDN, sempre atualizado)."""
    gist_id, token = _get_gist_config()
    if not gist_id or not token:
        raise ValueError("Sem gist_id ou token")
    api_url = f"https://api.github.com/gists/{gist_id}"
    req = Request(api_url)
    req.add_header("Authorization", f"token {token}")
    req.add_header("Accept", "application/vnd.github+json")
    with urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
        gist_data = json.loads(resp.read())
    content = gist_data.get("files", {}).get("licenses.json", {}).get("content", "")
    return json.loads(content) if content else {}


def _get_gist_config() -> tuple[str, str]:
    """Retorna (license_gist_id, token) do CONFIG.toml."""
    try:
        for candidate in [
            Path(os.getenv("APPDATA", "")) / "Fretio" / "CONFIG.toml",
            Path(getattr(sys, "_MEIPASS", "")) / "CONFIG.toml",
            Path(__file__).parent / "CONFIG.toml",
        ]:
            if not candidate.exists():
                continue
            cfg = _load_toml_file(candidate)
            fb = cfg.get("fretio", {})
            # Extrai o gist ID da license_url
            url = fb.get("license_url", "")
            gist_id = ""
            if url and "gist.githubusercontent.com" in url:
                # URL: https://gist.githubusercontent.com/USER/GIST_ID/raw/file
                parts = url.split("/")
                for i, p in enumerate(parts):
                    if p == "raw" and i >= 1:
                        gist_id = parts[i - 1]
                        break
            token = fb.get("error_report_token", "")
            if gist_id and token:
                return gist_id, token
    except Exception:
        pass
    return "", ""


def _register_machine(key: str, machine_id: str, gist_url: str) -> bool:
    """
    Registra o machine_id na licença (vincula chave à máquina).
    Atualiza o gist remoto via API do GitHub.
    Retorna True se conseguiu registrar.
    """
    gist_id, token = _get_gist_config()
    if not gist_id or not token:
        return False

    try:
        # 1. Buscar dados atuais via API (não CDN, para evitar cache stale)
        api_url = f"https://api.github.com/gists/{gist_id}"
        req = Request(api_url)
        req.add_header("Authorization", f"token {token}")
        req.add_header("Accept", "application/vnd.github+json")
        with urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
            gist_data = json.loads(resp.read())
        content = gist_data.get("files", {}).get("licenses.json", {}).get("content", "")
        data = json.loads(content) if content else {}

        lic_data = data.get("licenses", {}).get(key)
        if not lic_data:
            return False

        # 2. Adicionar máquina
        machines = lic_data.get("machines", [])
        if machine_id not in machines:
            machines.append(machine_id)
            lic_data["machines"] = machines
            data["licenses"][key] = lic_data

        # 3. Atualizar o gist via API
        api_url = f"https://api.github.com/gists/{gist_id}"
        payload = json.dumps({
            "files": {
                "licenses.json": {
                    "content": json.dumps(data, indent=2, ensure_ascii=False)
                }
            }
        }).encode("utf-8")
        req = Request(api_url, data=payload, method="PATCH")
        req.add_header("Authorization", f"token {token}")
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("Content-Type", "application/json")
        with urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
            return resp.status == 200
    except Exception:
        return False


def _save_validation_cache(key: str, status: LicenseStatus) -> None:
    """Salva cache da última validação bem-sucedida."""
    data = {
        "key": key,
        "valid": status.valid,
        "owner": status.owner,
        "blocked": status.blocked,
        "timestamp": time.time(),
    }
    _validation_cache_file().write_text(json.dumps(data), encoding="utf-8")


def _load_validation_cache(key: str) -> Optional[LicenseStatus]:
    """
    Carrega validação em cache se ainda dentro do período de graça.
    Retorna None se cache expirado ou inexistente.
    """
    f = _validation_cache_file()
    if not f.exists():
        return None
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        if data.get("key") != key:
            return None
        age_days = (time.time() - data.get("timestamp", 0)) / 86400
        if age_days > _GRACE_DAYS:
            return None
        if data.get("blocked"):
            return LicenseStatus(valid=False, blocked=True, message="Licença revogada.")
        if data.get("valid"):
            return LicenseStatus(
                valid=True,
                owner=data.get("owner", ""),
                message="Validado offline (sem conexão).",
                offline=True,
            )
    except Exception:
        pass
    return None


class LicenseClient:
    """Cliente HTTP para backend próprio de licenciamento."""

    def __init__(self, api_url: str) -> None:
        self.api_url = str(api_url or "").strip()

    def validate(self, key: str, machine_id: str) -> LicenseStatus:
        payload = json.dumps({
            "key": key,
            "machine_id": machine_id,
        }).encode("utf-8")
        req = Request(self.api_url, data=payload, method="POST", headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "Fretio-License/1.0",
        })
        with urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
            data = json.loads(resp.read())
        return self._status_from_response(data)

    @staticmethod
    def _status_from_response(data: Any) -> LicenseStatus:
        if isinstance(data, dict) and isinstance(data.get("license"), dict):
            data = data["license"]
        if isinstance(data, dict) and isinstance(data.get("status"), dict):
            data = data["status"]
        if not isinstance(data, dict):
            return LicenseStatus(valid=False, message="Resposta inválida do servidor de licença.")
        return LicenseStatus(
            valid=bool(data.get("valid", False)),
            owner=str(data.get("owner", "") or ""),
            message=str(data.get("message", "") or ""),
            blocked=bool(data.get("blocked", False)),
            expires=str(data.get("expires", "") or ""),
        )


class LicenseService:
    """Orquestra backend novo, fallback legado e cache offline."""

    _NETWORK_ERRORS = (URLError, OSError, json.JSONDecodeError)

    def validate(self, key: str, machine_id: str) -> LicenseStatus:
        api_url = _get_license_api_url()
        if api_url:
            return self._validate_backend(api_url, key, machine_id)
        return self._validate_legacy_gist(key, machine_id)

    def _offline_or_error(self, key: str) -> LicenseStatus:
        cached = _load_validation_cache(key)
        if cached:
            return cached
        return LicenseStatus(
            valid=False,
            message="Sem conexão para validar licença. Tente novamente com internet.",
        )

    def _validate_backend(self, api_url: str, key: str, machine_id: str) -> LicenseStatus:
        try:
            status = LicenseClient(api_url).validate(key, machine_id)
            if status.valid or status.blocked:
                _save_validation_cache(key, status)
            return status
        except self._NETWORK_ERRORS:
            return self._offline_or_error(key)

    def _validate_legacy_gist(self, key: str, machine_id: str) -> LicenseStatus:
        gist_url = _get_gist_url()
        if not gist_url:
            # Sem URL configurada → licença livre (sem sistema ativo)
            return LicenseStatus(valid=True, owner="(sem licenciamento)", message="")

        # Tentar validação online
        try:
            # Usa API (dados frescos) quando possível, fallback para CDN
            try:
                data = _fetch_licenses_fresh()
            except Exception:
                data = _fetch_licenses(gist_url)
            licenses: dict = data.get("licenses", {})
            blocked_keys: list = data.get("blocked_keys", [])
            blocked_machines: list = data.get("blocked_machines", [])

            # Verificar máquina bloqueada
            if machine_id in blocked_machines:
                status = LicenseStatus(
                    valid=False, blocked=True,
                    message="Esta máquina foi bloqueada. Contate o suporte.",
                )
                _save_validation_cache(key, status)
                return status

            # Verificar chave bloqueada
            if key in [k.strip().upper() for k in blocked_keys]:
                status = LicenseStatus(
                    valid=False, blocked=True,
                    message="Esta licença foi revogada. Contate o suporte.",
                )
                _save_validation_cache(key, status)
                return status

            # Verificar se chave existe
            lic_data = licenses.get(key)
            if not lic_data:
                return LicenseStatus(
                    valid=False,
                    message="Chave de licença inválida.",
                )

            # Verificar se está ativa
            if not lic_data.get("active", True):
                status = LicenseStatus(
                    valid=False, blocked=True,
                    owner=lic_data.get("owner", ""),
                    message="Licença desativada. Contate o suporte.",
                )
                _save_validation_cache(key, status)
                return status

            # Verificar expiração
            expires = lic_data.get("expires", "")
            if expires:
                from datetime import date
                try:
                    exp_date = date.fromisoformat(expires)
                    if date.today() > exp_date:
                        return LicenseStatus(
                            valid=False,
                            owner=lic_data.get("owner", ""),
                            expires=expires,
                            message=f"Licença expirou em {expires}.",
                        )
                except ValueError:
                    pass

            # Verificar binding de máquina
            bound_machines = lic_data.get("machines", [])
            max_machines = lic_data.get("max_machines", 1)

            if bound_machines and machine_id not in bound_machines:
                if len(bound_machines) >= max_machines:
                    return LicenseStatus(
                        valid=False,
                        owner=lic_data.get("owner", ""),
                        message="Esta licença já está ativada em outro computador.",
                    )

            # Registrar máquina se ainda não vinculada
            if machine_id not in bound_machines:
                _register_machine(key, machine_id, gist_url)

            # Tudo OK
            status = LicenseStatus(
                valid=True,
                owner=lic_data.get("owner", ""),
                expires=expires,
                message="Licença válida.",
            )
            _save_validation_cache(key, status)
            return status

        except self._NETWORK_ERRORS:
            # Sem conexão → tentar cache
            return self._offline_or_error(key)


def validate_license(key: str, machine_id: str = "") -> LicenseStatus:
    """
    Valida uma chave de licença contra o servidor remoto.
    Se offline, usa cache local (período de graça).
    """
    key = key.strip().upper()
    if not key:
        return LicenseStatus(valid=False, message="Nenhuma chave informada.")

    if not machine_id:
        machine_id = get_machine_id()

    return LicenseService().validate(key, machine_id)
