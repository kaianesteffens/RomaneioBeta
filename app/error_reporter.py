"""
FreteBot — Relatório de Erros Remoto.

Envia erros automaticamente para um GitHub Gist (como comentários).
Cada erro vira um comentário no gist, sem criar arquivos locais.
Falhas no envio são silenciosas — nunca impacta o uso do app.
Diagnóstico gravado em %APPDATA%/FreteBot/error_reporter.log.
"""
from __future__ import annotations

import json
import os
import platform
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

# ── Configurações (lidas do CONFIG.toml) ─────────────────────────
_gist_id: str = ""
_token: str = ""
_initialized = False

# ── Log de diagnóstico ────────────────────────────────────────────
_LOG_MAX_BYTES = 100 * 1024  # 100 KB — rotaciona apagando metade quando ultrapassar
_log_lock = threading.Lock()


def _log_path() -> Path:
    appdata = os.getenv("APPDATA", "")
    if appdata:
        return Path(appdata) / "FreteBot" / "error_reporter.log"
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


def _iter_config_candidates():
    """Gera candidatos de CONFIG.toml em ordem de preferência."""
    appdata = Path(os.getenv("APPDATA", ""))
    # Raiz do APPDATA (legado / futuro uso)
    yield appdata / "FreteBot" / "CONFIG.toml"
    # Pasta de empresas — varre todas e usa a primeira com as chaves
    empresas_dir = appdata / "FreteBot" / "empresas"
    if empresas_dir.exists():
        try:
            for emp_dir in sorted(empresas_dir.iterdir()):
                if emp_dir.is_dir():
                    yield emp_dir / "CONFIG.toml"
        except Exception:
            pass
    # Fallback: bundle PyInstaller e diretório do script
    meipass = getattr(sys, "_MEIPASS", "")
    if meipass:
        yield Path(meipass) / "CONFIG.toml"
    yield Path(__file__).parent / "CONFIG.toml"


def _load_config() -> None:
    """Carrega error_gist_id e error_report_token do CONFIG.toml."""
    global _gist_id, _token, _initialized
    if _initialized:
        return
    try:
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
            fb = cfg.get("fretebot", {})
            gist_id = fb.get("error_gist_id", "").strip()
            token = fb.get("error_report_token", "").strip()
            if gist_id and token:
                _gist_id = gist_id
                _token = token
                _initialized = True
                _diag("INFO", f"Config carregada de: {candidate} | gist_id={gist_id[:8]}...")
                return
            else:
                _diag("DEBUG", f"Config sem credenciais de report: {candidate} | gist_id={repr(gist_id)} token={'(presente)' if token else '(vazio)'}")
        # Nenhum arquivo tinha as chaves — NÃO marca como inicializado
        # para que a próxima chamada tente novamente (ex: config copiada depois)
        if candidates_checked:
            _diag("WARN", f"Nenhum CONFIG.toml com error_gist_id+error_report_token. Verificados: {candidates_checked}")
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
    global _gist_id, _token, _initialized
    _initialized = False
    try:
        p = Path(config_path)
        if not p.exists():
            _diag("WARN", f"configure(): arquivo não existe: {config_path}")
            return
        cfg = _load_toml_file(p)
        fb = cfg.get("fretebot", {})
        gist_id = fb.get("error_gist_id", "").strip()
        token = fb.get("error_report_token", "").strip()
        if gist_id and token:
            _gist_id = gist_id
            _token = token
            _initialized = True
            _diag("INFO", f"configure(): credenciais carregadas de {config_path} | gist_id={gist_id[:8]}...")
        else:
            _diag("WARN", f"configure(): {config_path} sem credenciais | gist_id={repr(gist_id)} token={'(presente)' if token else '(vazio)'} — tentará fallback em _load_config()")
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
        f = Path(os.getenv("APPDATA", "")) / "FreteBot" / "license.key"
        if f.exists():
            key = f.read_text(encoding="utf-8").strip()
            # Retorna só os primeiros 9 chars para privacidade (FBOT-XXXX)
            return key[:9] if len(key) > 9 else key
    except Exception:
        pass
    return "?"


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
    url = f"https://api.github.com/gists/{_gist_id}/comments"
    payload = json.dumps({"body": body}).encode("utf-8")
    req = Request(url, data=payload, method="POST")
    # Bearer funciona para classic PATs e fine-grained PATs; "token" só para classic
    req.add_header("Authorization", f"Bearer {_token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("Content-Type", "application/json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    try:
        with urlopen(req, timeout=15) as resp:
            ok = resp.status == 201
            if ok:
                _diag("INFO", f"_send_to_gist({label}): enviado com sucesso (HTTP 201)")
            else:
                _diag("WARN", f"_send_to_gist({label}): resposta inesperada HTTP {resp.status}")
            return ok
    except HTTPError as e:
        body_snippet = ""
        try:
            body_snippet = e.read(200).decode("utf-8", errors="replace")
        except Exception:
            pass
        _diag("ERROR", f"_send_to_gist({label}): HTTP {e.code} {e.reason} | gist_id={_gist_id[:8]}... | resposta: {body_snippet}")
        return False
    except URLError as e:
        _diag("ERROR", f"_send_to_gist({label}): URLError — {e.reason}")
        return False
    except Exception as e:
        _diag("ERROR", f"_send_to_gist({label}): exceção inesperada — {type(e).__name__}: {e}")
        return False


def report_error(
    exc_type: type | None = None,
    exc_value: BaseException | None = None,
    exc_tb=None,
    context: str = "",
    wait: bool = False,
) -> None:
    """
    Envia um erro para o GitHub Gist.

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
        if not _gist_id or not _token:
            _diag("WARN", f"report_error({exc_type_name}): sem credenciais — descartado")
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

        body_parts = [
            f"## {exc_type_name}: {str(exc_value)[:200]}",
            "",
            f"| Campo | Valor |",
            f"|-------|-------|",
            f"| Versão | `{version}` |",
            f"| Máquina | `{machine}` |",
            f"| Licença | `{license_id}` |",
            f"| OS | `{os_info}` |",
            f"| Data/Hora | `{timestamp}` |",
            f"| Contexto | `{context or 'N/A'}` |",
            f"| Fingerprint | `{fp}` |",
            "",
            "### Traceback",
            "```python",
            tb_text.strip(),
            "```",
        ]
        body = "\n".join(body_parts)

        # Envia em thread separada para não bloquear o app
        label = f"{exc_type_name}/{context or 'N/A'}"
        t = threading.Thread(target=_send_to_gist, args=(body, label), daemon=True)
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
        if not _gist_id or not _token:
            _diag("WARN", f"report_error_message: sem credenciais — descartado: {message[:80]}")
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

        body_parts = [
            f"## ⚠️ {message[:200]}",
            "",
            f"| Campo | Valor |",
            f"|-------|-------|",
            f"| Versão | `{version}` |",
            f"| Máquina | `{machine}` |",
            f"| Licença | `{license_id}` |",
            f"| Data/Hora | `{timestamp}` |",
            f"| Contexto | `{context or 'N/A'}` |",
        ]
        body = "\n".join(body_parts)

        label = f"msg/{context or 'N/A'}"
        t = threading.Thread(target=_send_to_gist, args=(body, label), daemon=True)
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
        report_error(
            args.exc_type, args.exc_value, args.exc_traceback,
            context=f"thread:{args.thread}",
        )
        if _original_threading_excepthook:
            try:
                _original_threading_excepthook(args)
            except Exception:
                pass

    threading.excepthook = _thread_excepthook
    _diag("INFO", "install_global_hooks(): hooks instalados (sys.excepthook + threading.excepthook)")
