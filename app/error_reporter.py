"""
Fretio — Relatório de Erros Remoto.

Envia erros automaticamente para o servidor próprio ou, temporariamente,
para um GitHub Gist como fallback legado. Falhas no envio são silenciosas —
nunca impactam o uso do app. Diagnóstico gravado em
%APPDATA%/Fretio/error_reporter.log.
"""
from __future__ import annotations

import json
import os
import platform
import re
import ssl
import sys
import time
import traceback
import threading
from hashlib import sha256
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


# ── Rate-limit: no máximo 1 report por erro idêntico a cada 10min ───
_RATE_LIMIT_SECONDS = 600
_recent_errors: dict[str, float] = {}
_lock = threading.Lock()
_CONFIG_SECTIONS = ("fretio", "fretebot", "romaneio")
_ENV_ERROR_API_URL_VARS = ("FRETIO_ERROR_API_URL", "FRETEBOT_ERROR_API_URL")
_ENV_GIST_ID_VARS = ("FRETIO_ERROR_GIST_ID", "FRETEBOT_ERROR_GIST_ID")
_ENV_TOKEN_VARS = ("FRETIO_ERROR_REPORT_TOKEN", "FRETEBOT_ERROR_REPORT_TOKEN")
_invalid_token_fingerprints: set[str] = set()

# ── Configurações (lidas do CONFIG.toml) ─────────────────────────
_error_api_url: str = ""
_gist_id: str = ""
_token: str = ""
_initialized = False

# Fallback global embutido no código (opcional).
# Preencha no build/release para centralizar o reporte em todas as máquinas,
# sem depender de CONFIG.toml local.
_EMBEDDED_ERROR_GIST_ID: str = ""
_EMBEDDED_ERROR_REPORT_TOKEN: str = ""

# ── Log de diagnóstico ────────────────────────────────────────────
_LOG_MAX_BYTES = 100 * 1024  # 100 KB — rotaciona apagando metade quando ultrapassar
_log_lock = threading.Lock()


def _ssl_context() -> ssl.SSLContext:
    try:
        import certifi  # type: ignore[import-untyped]

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def sanitize_error_payload(text: str) -> str:
    """Remove dados sensíveis de payloads de erro antes do envio remoto."""
    sanitized = str(text or "")

    secret_field_names = (
        "admin_token",
        "database_url",
        "senha",
        "password",
        "token",
        "error_report_token",
    )
    license_field_names = (
        "license",
        "license_key",
        "licenca",
        "licença",
    )
    field_pattern = "|".join(re.escape(name) for name in secret_field_names + license_field_names)

    def _redact_field(match: re.Match) -> str:
        field_name = match.group(1).casefold()
        if field_name in {"admin_token", "database_url"}:
            return "[TOKEN_REDACTED]"
        marker = "[LICENSE_REDACTED]" if field_name in {name.casefold() for name in license_field_names} else "[TOKEN_REDACTED]"
        return f"{match.group(1)}{match.group(2)}{match.group(3)}{marker}"

    sanitized = re.sub(
        rf"(?i)\b({field_pattern})\b(\s*[:=]\s*)([`'\"]?)([^`'\"\s,;|]+)",
        _redact_field,
        sanitized,
    )

    sanitized = re.sub(
        r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+",
        "Bearer [TOKEN_REDACTED]",
        sanitized,
    )
    sanitized = re.sub(r"\bghp_[A-Za-z0-9_]{20,}\b", "[TOKEN_REDACTED]", sanitized)
    sanitized = re.sub(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b", "[TOKEN_REDACTED]", sanitized)
    sanitized = re.sub(
        r"(?i)([?&](?:token|access_token|auth|key|password|senha|license|licenca|licen%C3%A7a)=)[^&#\s]+",
        r"\1[TOKEN_REDACTED]",
        sanitized,
    )
    sanitized = re.sub(
        r"(?i)\bhttps?://[^\s`'\"<>]*(?:token|access_token|auth|key|password|senha|license|licenca|licen%C3%A7a)=[^\s`'\"<>]+",
        "[URL_REDACTED]",
        sanitized,
    )

    sanitized = re.sub(
        r"\bFBOT-[A-Z0-9]{4}(?:-[A-Z0-9]{4}){0,5}\b",
        "[LICENSE_REDACTED]",
        sanitized,
        flags=re.IGNORECASE,
    )
    sanitized = re.sub(
        r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b",
        "[EMAIL_REDACTED]",
        sanitized,
    )
    sanitized = re.sub(
        r"\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b",
        "[CNPJ_REDACTED]",
        sanitized,
    )
    sanitized = re.sub(
        r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b",
        "[CPF_REDACTED]",
        sanitized,
    )
    sanitized = re.sub(
        r"\b\d{5}-?\d{3}\b",
        "[CEP_REDACTED]",
        sanitized,
    )
    return sanitized


def _log_path() -> Path:
    appdata = os.getenv("APPDATA", "")
    if appdata:
        return Path(appdata) / "Fretio" / "error_reporter.log"
    return Path(__file__).parent / "error_reporter.log"


def _diag(level: str, msg: str) -> None:
    """Grava linha de diagnóstico no log local. Nunca lança exceção."""
    try:
        p = _log_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{ts}] [{level}] {msg}\n"
        with _log_lock:
            # Rotação simples: se ultrapassar limite, mantém só a metade final
            if p.exists() and p.stat().st_size > _LOG_MAX_BYTES:
                content = p.read_bytes()
                p.write_bytes(content[len(content) // 2:])
            with p.open("a", encoding="utf-8") as f:
                f.write(line)
    except Exception:
        pass


def _load_toml_file(path: Path) -> dict:
    """Carrega TOML aceitando UTF-8 com/sem BOM."""
    raw = path.read_text(encoding="utf-8-sig")
    data = None
    # tomllib é built-in no Python 3.11+
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
        try:
            import tomli as _tomli  # type: ignore[import-not-found]
            data = _tomli.loads(raw)
        except ImportError:
            pass
    if data is None:
        raise ImportError("Nenhuma biblioteca TOML disponível (tomllib/toml/tomli)")
    return data if isinstance(data, dict) else {}


def _token_fingerprint(token: str) -> str:
    return sha256(str(token or "").encode("utf-8")).hexdigest()[:16]


def _is_token_blocklisted(token: str) -> bool:
    token = str(token or "").strip()
    if not token:
        return False
    return _token_fingerprint(token) in _invalid_token_fingerprints


def _remember_invalid_current_token(reason: str = "") -> str:
    global _gist_id, _token, _initialized
    current_token = str(_token or "").strip()
    if not current_token:
        return ""
    fp = _token_fingerprint(current_token)
    _invalid_token_fingerprints.add(fp)
    _diag("WARN", f"Token de reporte invalidado{f' ({reason})' if reason else ''} | fp={fp} | gist_id={_gist_id[:8]}...")
    _gist_id = ""
    _token = ""
    _initialized = False
    return fp


def _apply_error_api_url(url: str, *, source: str) -> bool:
    global _error_api_url
    url = str(url or "").strip()
    if not url:
        return False
    _error_api_url = url
    _diag("INFO", f"error_api_url carregado de {source}")
    return True


def _apply_credentials(gist_id: str, token: str, *, source: str) -> bool:
    global _gist_id, _token
    gist_id = str(gist_id or "").strip()
    token = str(token or "").strip()
    if not gist_id or not token:
        return False
    if _is_token_blocklisted(token):
        _diag("WARN", f"Ignorando credenciais bloqueadas de {source} | gist_id={gist_id[:8]}... | fp={_token_fingerprint(token)}")
        return False
    _gist_id = gist_id
    _token = token
    return True


def _iter_config_candidates():
    """Gera candidatos de CONFIG.toml em ordem de preferência."""
    appdata = Path(os.getenv("APPDATA", ""))
    # Caminhos atuais
    yield appdata / "Fretio" / "CONFIG.toml"
    empresas_dir = appdata / "Fretio" / "empresas"
    if empresas_dir.exists():
        try:
            for emp_dir in sorted(empresas_dir.iterdir()):
                if emp_dir.is_dir():
                    yield emp_dir / "CONFIG.toml"
        except Exception:
            pass

    # Fallback legado (pré-renomeação): %APPDATA%\FreteBot
    # Mantém compatibilidade caso a migração tenha sido parcial.
    yield appdata / "FreteBot" / "CONFIG.toml"
    legacy_empresas_dir = appdata / "FreteBot" / "empresas"
    if legacy_empresas_dir.exists():
        try:
            for emp_dir in sorted(legacy_empresas_dir.iterdir()):
                if emp_dir.is_dir():
                    yield emp_dir / "CONFIG.toml"
        except Exception:
            pass

    # Fallback: bundle PyInstaller e diretório do script
    meipass = getattr(sys, "_MEIPASS", "")
    if meipass:
        yield Path(meipass) / "CONFIG.toml"
    yield Path(__file__).parent / "CONFIG.toml"


def _load_env_fallback() -> bool:
    """Carrega endpoint/credenciais do ambiente, se disponíveis."""
    loaded = False
    for env_name in _ENV_ERROR_API_URL_VARS:
        url = os.getenv(env_name, "").strip()
        if url:
            loaded = _apply_error_api_url(url, source=f"ambiente:{env_name}") or loaded
            break

    gist_id = ""
    token = ""
    for env_name in _ENV_GIST_ID_VARS:
        gist_id = os.getenv(env_name, "").strip()
        if gist_id:
            break
    for env_name in _ENV_TOKEN_VARS:
        token = os.getenv(env_name, "").strip()
        if token:
            break
    if _apply_credentials(gist_id, token, source="ambiente"):
        _diag("INFO", f"Credenciais carregadas do ambiente | gist_id={gist_id[:8]}...")
        loaded = True
    return loaded


def _load_embedded_fallback() -> bool:
    """Carrega credenciais embutidas no binário, se disponíveis."""
    gist_id = str(_EMBEDDED_ERROR_GIST_ID or "").strip()
    token = str(_EMBEDDED_ERROR_REPORT_TOKEN or "").strip()
    if _apply_credentials(gist_id, token, source="fallback embutido"):
        _diag("INFO", f"Credenciais carregadas do fallback embutido | gist_id={gist_id[:8]}...")
        return True
    return False


def _read_recent_diag_log(max_bytes: int = 12_000) -> str:
    try:
        p = _log_path()
        if not p.exists():
            return ""
        raw = p.read_bytes()
        tail = raw[-max_bytes:] if len(raw) > max_bytes else raw
        return tail.decode("utf-8", errors="replace").strip()
    except Exception:
        return ""


def _load_config() -> None:
    """Carrega error_api_url e fallback Gist do ambiente/CONFIG.toml."""
    global _initialized
    if _initialized:
        return
    try:
        loaded = bool(_error_api_url or (_gist_id and _token))
        loaded = _load_env_fallback() or loaded
        if not (_gist_id and _token):
            loaded = _load_embedded_fallback() or loaded

        candidates_checked = []
        for candidate in _iter_config_candidates():
            if not candidate.exists():
                continue
            candidates_checked.append(str(candidate))
            try:
                cfg = _load_toml_file(candidate)
            except Exception as e:
                _diag("WARN", f"Falha ao ler {candidate}: {e}")
                continue
            for section_name in _CONFIG_SECTIONS:
                fb = cfg.get(section_name, {})
                if not isinstance(fb, dict):
                    continue
                error_api_url = str(fb.get("error_api_url", "")).strip()
                if error_api_url and not _error_api_url:
                    loaded = _apply_error_api_url(error_api_url, source=f"{candidate} [{section_name}]") or loaded
                gist_id = str(fb.get("error_gist_id", "")).strip()
                token = str(fb.get("error_report_token", "")).strip()
                if (not _gist_id or not _token) and _apply_credentials(gist_id, token, source=str(candidate)):
                    _diag("INFO", f"Config carregada de: {candidate} [{section_name}] | gist_id={gist_id[:8]}...")
                    loaded = True
            _diag("DEBUG", f"Config verificada para report: {candidate}")

        if loaded:
            _initialized = True
            return

        # Nenhum arquivo tinha as chaves — NÃO marca como inicializado
        # para que a próxima chamada tente novamente (ex: config copiada depois)
        if candidates_checked:
            _diag("WARN", f"Nenhum CONFIG.toml com error_api_url ou credenciais Gist. Verificados: {candidates_checked}")
        else:
            _diag("WARN", "Nenhum CONFIG.toml encontrado em nenhum caminho candidato.")
    except Exception as e:
        _diag("ERROR", f"_load_config falhou inesperadamente: {e}")


def reload_config() -> None:
    """Força recarregamento da configuração (útil após setup inicial do app)."""
    global _initialized
    _initialized = False
    _load_config()


def configure(config_path) -> None:
    """Configura o error reporter com o path explícito do CONFIG.toml da empresa ativa."""
    global _initialized
    _initialized = False
    try:
        p = Path(config_path)
        if not p.exists():
            _diag("WARN", f"configure(): arquivo não existe: {config_path}")
            return
        cfg = _load_toml_file(p)
        for section_name in _CONFIG_SECTIONS:
            fb = cfg.get(section_name, {})
            if not isinstance(fb, dict):
                continue
            loaded = False
            error_api_url = str(fb.get("error_api_url", "")).strip()
            if error_api_url:
                loaded = _apply_error_api_url(error_api_url, source=f"{config_path} [{section_name}]") or loaded
            gist_id = str(fb.get("error_gist_id", "")).strip()
            token = str(fb.get("error_report_token", "")).strip()
            if _apply_credentials(gist_id, token, source=str(config_path)):
                _diag("INFO", f"configure(): credenciais carregadas de {config_path} [{section_name}] | gist_id={gist_id[:8]}...")
                loaded = True
            if loaded and _error_api_url and _gist_id and _token:
                _initialized = True
                return
        _load_config()
        if _initialized:
            _diag("INFO", f"configure(): usando configuração global/fallback para {config_path}")
        else:
            _diag("WARN", f"configure(): {config_path} sem endpoint/credenciais — tentará fallback em _load_config()")
        # Se as chaves não existem/estão vazias: _initialized permanece False
        # para que _load_config() possa tentar os caminhos de fallback
    except Exception as e:
        _diag("ERROR", f"configure() falhou: {e}")


def _get_version() -> str:
    try:
        p = Path(getattr(sys, "_MEIPASS", "")) / "version.txt"
        if p.exists():
            return p.read_text().strip()
        p = Path(__file__).parent / "version.txt"
        if p.exists():
            return p.read_text().strip()
    except Exception:
        pass
    return "?"


def _get_machine_hash() -> str:
    """Retorna hash parcial da máquina (sem expor dados sensíveis)."""
    try:
        node = platform.node()
        user = os.getenv("USERNAME", "?")
        return sha256(f"{node}|{user}".encode()).hexdigest()[:12]
    except Exception:
        return "unknown"


def _get_license_key() -> str:
    """Lê a chave de licença salva (para identificar o cliente)."""
    try:
        f = Path(os.getenv("APPDATA", "")) / "Fretio" / "license.key"
        if f.exists():
            key = f.read_text(encoding="utf-8").strip()
            # Retorna só os primeiros 9 chars para privacidade (FBOT-XXXX)
            return key[:9] if len(key) > 9 else key
    except Exception:
        pass
    return "?"


def _get_saved_license_key() -> str:
    """Lê a licença salva completa para identificação no servidor próprio."""
    try:
        from license import get_saved_license  # type: ignore[import-not-found]

        key = str(get_saved_license() or "").strip()
        if key:
            return key
    except Exception:
        pass
    try:
        f = Path(os.getenv("APPDATA", "")) / "Fretio" / "license.key"
        if f.exists():
            key = f.read_text(encoding="utf-8").strip()
            if key:
                return key
    except Exception:
        pass
    return ""


def _get_machine_id_for_report() -> str:
    """Usa o mesmo machine_id do licenciamento quando disponível."""
    try:
        from license import get_machine_id  # type: ignore[import-not-found]

        machine_id = str(get_machine_id() or "").strip()
        if machine_id:
            return machine_id
    except Exception:
        pass
    return _get_machine_hash()


def _error_fingerprint(exc_type_name: str, tb_text: str) -> str:
    """Gera hash do erro para deduplicação/rate-limit."""
    # Normaliza o traceback removendo números de linha específicos
    normalized = ""
    for line in tb_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("File "):
            # Mantém só nome do arquivo e função, não número de linha
            parts = stripped.split(",")
            normalized += parts[0] + (parts[-1] if len(parts) > 1 else "") + "\n"
        else:
            normalized += stripped + "\n"
    return sha256(f"{exc_type_name}:{normalized}".encode()).hexdigest()[:16]


def _is_rate_limited(fingerprint: str) -> bool:
    """Verifica se esse erro já foi reportado recentemente."""
    now = time.time()
    with _lock:
        last = _recent_errors.get(fingerprint, 0)
        if now - last < _RATE_LIMIT_SECONDS:
            return True
        _recent_errors[fingerprint] = now
        # Limpa entradas antigas
        expired = [k for k, v in _recent_errors.items() if now - v > _RATE_LIMIT_SECONDS * 2]
        for k in expired:
            del _recent_errors[k]
    return False


def _send_to_gist(body: str, label: str = "") -> bool:
    """Envia um comentário ao Gist via API do GitHub."""
    if not _gist_id or not _token:
        _diag("WARN", f"_send_to_gist({label}): abortado — gist_id ou token vazios no momento do envio")
        return False

    def _send_once() -> bool:
        url = f"https://api.github.com/gists/{_gist_id}/comments"
        payload = json.dumps({"body": body}).encode("utf-8")
        req = Request(url, data=payload, method="POST")
        # Bearer funciona para classic PATs e fine-grained PATs; "token" só para classic
        req.add_header("Authorization", f"Bearer {_token}")
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("Content-Type", "application/json")
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
        with urlopen(req, timeout=15, context=_ssl_context()) as resp:
            ok = resp.status == 201
            if ok:
                _diag("INFO", f"_send_to_gist({label}): enviado com sucesso (HTTP 201)")
            else:
                _diag("WARN", f"_send_to_gist({label}): resposta inesperada HTTP {resp.status}")
            return ok

    try:
        return _send_once()
    except HTTPError as e:
        body_snippet = ""
        try:
            body_snippet = e.read(200).decode("utf-8", errors="replace")
        except Exception:
            pass
        _diag("ERROR", f"_send_to_gist({label}): HTTP {e.code} {e.reason} | gist_id={_gist_id[:8]}... | resposta: {body_snippet}")
        if e.code == 401:
            previous_fp = _remember_invalid_current_token("http-401")
            reload_config()
            if _gist_id and _token and _token_fingerprint(_token) != previous_fp:
                _diag("INFO", f"_send_to_gist({label}): tentando fallback após 401")
                try:
                    return _send_once()
                except HTTPError as retry_err:
                    retry_body = ""
                    try:
                        retry_body = retry_err.read(200).decode("utf-8", errors="replace")
                    except Exception:
                        pass
                    _diag("ERROR", f"_send_to_gist({label}): fallback HTTP {retry_err.code} {retry_err.reason} | gist_id={_gist_id[:8]}... | resposta: {retry_body}")
                except URLError as retry_err:
                    _diag("ERROR", f"_send_to_gist({label}): fallback URLError — {retry_err.reason}")
                except Exception as retry_err:
                    _diag("ERROR", f"_send_to_gist({label}): fallback exceção inesperada — {type(retry_err).__name__}: {retry_err}")
        return False
    except URLError as e:
        _diag("ERROR", f"_send_to_gist({label}): URLError — {e.reason}")
        return False
    except Exception as e:
        _diag("ERROR", f"_send_to_gist({label}): exceção inesperada — {type(e).__name__}: {e}")
        return False


def _build_error_api_payload(
    *,
    module: str,
    message: str,
    traceback_text: str,
) -> dict[str, str]:
    return {
        "license_key": _get_saved_license_key(),
        "machine_id": _get_machine_id_for_report(),
        "app_version": _get_version(),
        "module": sanitize_error_payload(module or ""),
        "provider": "",
        "message": sanitize_error_payload(message or ""),
        "traceback": sanitize_error_payload(traceback_text or ""),
    }


def _send_to_error_api(payload: dict[str, str], label: str = "") -> bool:
    """Envia erro ao servidor próprio. Nunca propaga exceção."""
    if not _error_api_url:
        return False
    try:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = Request(_error_api_url, data=data, method="POST", headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "Fretio-ErrorReporter/1.0",
        })
        with urlopen(req, timeout=15, context=_ssl_context()) as resp:
            ok = 200 <= int(getattr(resp, "status", 0) or 0) < 300
            if ok:
                _diag("INFO", f"_send_to_error_api({label}): enviado com sucesso (HTTP {resp.status})")
            else:
                _diag("WARN", f"_send_to_error_api({label}): resposta inesperada HTTP {resp.status}")
            return ok
    except HTTPError as e:
        _diag("ERROR", f"_send_to_error_api({label}): HTTP {e.code} {e.reason}")
        return False
    except URLError as e:
        _diag("ERROR", f"_send_to_error_api({label}): URLError — {e.reason}")
        return False
    except Exception as e:
        _diag("ERROR", f"_send_to_error_api({label}): exceção inesperada — {type(e).__name__}: {e}")
        return False


def _send_report(body: str, label: str = "", api_payload: dict[str, str] | None = None) -> bool:
    """Prioriza API própria e usa Gist como fallback legado quando disponível."""
    if _error_api_url and api_payload is not None:
        if _send_to_error_api(api_payload, label=label):
            return True
        _diag("WARN", f"_send_report({label}): API de erros falhou; tentando fallback Gist")
    if _gist_id and _token:
        return _send_to_gist(body, label=label)
    return False


def report_error(
    exc_type: type | None = None,
    exc_value: BaseException | None = None,
    exc_tb=None,
    context: str = "",
    wait: bool = False,
) -> None:
    """
    Envia um erro para o servidor próprio ou fallback Gist.

    Pode ser chamado diretamente ou como sys.excepthook.
    Falhas no envio são silenciosas.
    Se wait=True, bloqueia até o envio completar (para crashes fatais).
    """
    try:
        if exc_type is None and exc_value is None:
            # Usa a exceção atual do sys.exc_info()
            exc_type, exc_value, exc_tb = sys.exc_info()

        if exc_type is None:
            return

        # Não reportar KeyboardInterrupt ou SystemExit
        if issubclass(exc_type, (KeyboardInterrupt, SystemExit)):
            return

        exc_type_name = getattr(exc_type, "__name__", str(exc_type))
        tb_text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))

        # Garante config carregada antes de verificar rate-limit
        # (rate-limit só deve consumir slot se o envio for possível)
        _load_config()
        if not _error_api_url and not (_gist_id and _token):
            _diag("WARN", f"report_error({exc_type_name}): sem endpoint/credenciais — descartado")
            return

        # Rate-limit por fingerprint
        fp = _error_fingerprint(exc_type_name, tb_text)
        if _is_rate_limited(fp):
            _diag("DEBUG", f"report_error({exc_type_name}): rate-limited (fp={fp})")
            return

        _diag("INFO", f"report_error({exc_type_name}): enviando... context={context or 'N/A'} fp={fp}")

        # Monta o corpo do comentário (Markdown)
        version = _get_version()
        machine = _get_machine_hash()
        license_id = _get_license_key()
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        os_info = f"{platform.system()} {platform.release()} ({platform.version()})"

        exc_msg_full = str(exc_value)
        body_parts = [
            f"## {exc_type_name}: {exc_msg_full[:200]}",
            "",
            f"| Campo | Valor |",
            f"|-------|-------|",
            f"| Versão | `{version}` |",
            f"| Python | `{sys.version.split()[0]}` |",
            f"| Máquina | `{machine}` |",
            f"| Licença | `{license_id}` |",
            f"| OS | `{os_info}` |",
            f"| Data/Hora | `{timestamp}` |",
            f"| Contexto | `{context or 'N/A'}` |",
            f"| Fingerprint | `{fp}` |",
        ]
        if len(exc_msg_full) > 200:
            body_parts += [
                "",
                "### Mensagem Completa",
                "```",
                exc_msg_full,
                "```",
            ]
        body_parts += [
            "",
            "### Traceback",
            "```python",
            tb_text.strip(),
            "```",
        ]
        recent_diag = _read_recent_diag_log()
        if recent_diag:
            body_parts += [
                "",
                "### Diagnostico Local Recente",
                "```text",
                recent_diag,
                "```",
            ]
        body = sanitize_error_payload("\n".join(body_parts))
        api_payload = _build_error_api_payload(
            module=context or "",
            message=f"{exc_type_name}: {exc_msg_full}",
            traceback_text=tb_text,
        )

        # Envia em thread separada para não bloquear o app
        label = f"{exc_type_name}/{context or 'N/A'}"
        t = threading.Thread(target=_send_report, args=(body, label, api_payload), daemon=True)
        t.start()
        if wait:
            t.join(timeout=20)

    except Exception:
        pass  # Falha silenciosa — error reporting NUNCA deve crashar o app


def report_error_message(message: str, context: str = "", wait: bool = False) -> None:
    """
    Envia uma mensagem de erro customizada (sem exceção Python).
    Útil para erros de lógica ou condições inesperadas.
    Se wait=True, bloqueia até o envio completar.
    """
    try:
        _load_config()
        if not _error_api_url and not (_gist_id and _token):
            _diag("WARN", f"report_error_message: sem endpoint/credenciais — descartado: {message[:80]}")
            return

        fp = sha256(message.encode()).hexdigest()[:16]
        if _is_rate_limited(fp):
            _diag("DEBUG", f"report_error_message: rate-limited (fp={fp})")
            return

        _diag("INFO", f"report_error_message: enviando... context={context or 'N/A'}")

        version = _get_version()
        machine = _get_machine_hash()
        license_id = _get_license_key()
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        os_info = f"{platform.system()} {platform.release()} ({platform.version()})"
        caller_stack = "".join(traceback.format_stack()[:-1]).strip()

        body_parts = [
            f"## ⚠️ {message[:200]}",
            "",
            f"| Campo | Valor |",
            f"|-------|-------|",
            f"| Versão | `{version}` |",
            f"| Python | `{sys.version.split()[0]}` |",
            f"| Máquina | `{machine}` |",
            f"| Licença | `{license_id}` |",
            f"| OS | `{os_info}` |",
            f"| Data/Hora | `{timestamp}` |",
            f"| Contexto | `{context or 'N/A'}` |",
            "",
            "### Stack de Chamada",
            "```python",
            caller_stack,
            "```",
        ]
        recent_diag = _read_recent_diag_log()
        if recent_diag:
            body_parts += [
                "",
                "### Diagnostico Local Recente",
                "```text",
                recent_diag,
                "```",
            ]
        body = sanitize_error_payload("\n".join(body_parts))
        api_payload = _build_error_api_payload(
            module=context or "",
            message=f"message: {message}",
            traceback_text=caller_stack,
        )

        label = f"msg/{context or 'N/A'}"
        t = threading.Thread(target=_send_report, args=(body, label, api_payload), daemon=True)
        t.start()
        if wait:
            t.join(timeout=20)

    except Exception:
        pass


# ── Hooks globais ───────────────────────────────────────────────
_original_excepthook = None
_original_threading_excepthook = None


def install_global_hooks() -> None:
    """
    Instala hooks globais para capturar exceções não tratadas.
    Chame uma vez na inicialização do app.
    """
    global _original_excepthook, _original_threading_excepthook

    _diag("INFO", f"install_global_hooks(): inicializando | versão={_get_version()}")

    # Carrega config cedo para detectar problemas
    _load_config()

    # Hook principal (exceções não tratadas no thread principal)
    _original_excepthook = sys.excepthook

    def _sys_excepthook(exc_type, exc_value, exc_tb):
        # wait=True pois após o excepthook o processo pode morrer
        report_error(exc_type, exc_value, exc_tb, context="sys.excepthook", wait=True)
        if _original_excepthook:
            _original_excepthook(exc_type, exc_value, exc_tb)

    sys.excepthook = _sys_excepthook

    # Hook de threading (exceções não tratadas em threads)
    _original_threading_excepthook = threading.excepthook

    def _thread_excepthook(args):
        thread_name = getattr(args.thread, "name", None) or str(args.thread)
        report_error(
            args.exc_type, args.exc_value, args.exc_traceback,
            context=f"thread:{thread_name}",
        )
        if _original_threading_excepthook:
            try:
                _original_threading_excepthook(args)
            except Exception:
                pass

    threading.excepthook = _thread_excepthook
    _diag("INFO", "install_global_hooks(): hooks instalados (sys.excepthook + threading.excepthook)")
