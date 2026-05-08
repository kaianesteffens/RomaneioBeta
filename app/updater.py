"""
Fretio Auto-Updater via GitHub Releases.

Verifica se há nova versão disponível no GitHub e atualiza os arquivos
do aplicativo automaticamente, sem precisar gerar um novo instalador.

Uso:
    from updater import check_for_update, apply_update

    info = check_for_update("owner/repo", "1.21")
    if info:
        apply_update(info, callback=print)
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Optional, Sequence
from urllib.error import URLError
from urllib.request import Request, urlopen

from update_security import verify_update_signature

# Timeout para requisições HTTP (segundos)
_HTTP_TIMEOUT = 30
_DEFAULT_GITHUB_REPOS = ("kaianesteffens/RomaneioBeta-releases",)
_GITHUB_REPO_ENV_VARS = (
    "FRETIO_GITHUB_REPO",
    "FRETEBOT_GITHUB_REPO",
    "Fretio_GITHUB_REPO",
)
_GITHUB_REPO_ALIAS_ENV_VARS = (
    "FRETIO_GITHUB_REPO_ALIASES",
    "FRETEBOT_GITHUB_REPO_ALIASES",
    "Fretio_GITHUB_REPO_ALIASES",
)
_GITHUB_REPO_CONFIG_SECTIONS = ("fretio", "fretebot", "romaneio")
_PREFERRED_UPDATE_ASSET_NAMES = (
    "fretio-update-latest.zip",
    "fretebot-update-latest.zip",
    "romaneiobeta-update-latest.zip",
)


@dataclass
class UpdateInfo:
    """Dados de uma atualização disponível."""
    tag: str                  # Ex: "1.22"
    version: str              # Ex: "1.22"
    download_url: str         # URL do asset ZIP
    asset_name: str           # Nome do arquivo ZIP
    asset_size: int           # Bytes
    release_notes: str        # Corpo da release (Markdown)
    html_url: str             # URL da release no GitHub
    source_repo: str = ""     # Repositório usado para resolver a release
    signature_download_url: str = ""  # URL do asset .sig
    signature_asset_name: str = ""    # Nome do asset .sig


def _load_toml_file(path: Path) -> dict[str, Any]:
    """Carrega TOML aceitando UTF-8 com/sem BOM."""
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


def _github_api(url: str) -> Any:
    """Faz GET na API do GitHub e retorna JSON."""
    req = Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "Fretio-Updater/1.0",
    })
    with urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
        return json.loads(resp.read())


def _parse_version(tag: str) -> tuple[int, ...]:
    """Converte '1.22', 'v1.22' ou '1.22-beta' em (1, 22) para comparação."""
    import re as _re
    tag = tag.lstrip("vV").strip()
    parts: list[int] = []
    for p in tag.split("."):
        m = _re.match(r"(\d+)", p)
        if m:
            parts.append(int(m.group(1)))
        else:
            break
    return tuple(parts) if parts else (0,)


def _split_repo_candidates(raw: Any) -> list[str]:
    if isinstance(raw, str):
        normalized = raw.replace(";", ",").replace("\n", ",")
        items = [item.strip() for item in normalized.split(",")]
    elif isinstance(raw, (list, tuple, set)):
        items = [str(item).strip() for item in raw]
    else:
        return []
    return [item for item in items if item and "/" in item]


def _dedupe_repos(repos: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for repo in repos:
        normalized = repo.strip()
        if not normalized or "/" not in normalized:
            continue
        lowered = normalized.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        ordered.append(normalized)
    return ordered


def _select_update_asset(assets: Sequence[dict[str, Any]]) -> dict[str, Any] | None:
    zip_assets = [
        asset for asset in assets
        if str(asset.get("name", "")).lower().endswith(".zip")
    ]
    if not zip_assets:
        return None

    def _asset_rank(asset: dict[str, Any]) -> tuple[int, int, str]:
        name = str(asset.get("name", "")).strip()
        lowered = name.lower()
        if lowered in _PREFERRED_UPDATE_ASSET_NAMES:
            return (0, _PREFERRED_UPDATE_ASSET_NAMES.index(lowered), lowered)
        if "update" in lowered:
            return (1, 0, lowered)
        if "fretio" in lowered or "fretebot" in lowered or "romaneio" in lowered:
            return (2, 0, lowered)
        return (3, 0, lowered)

    return min(zip_assets, key=_asset_rank)


def _select_signature_asset(
    assets: Sequence[dict[str, Any]],
    zip_asset_name: str,
) -> dict[str, Any] | None:
    expected_name = f"{zip_asset_name}.sig".lower()
    for asset in assets:
        if str(asset.get("name", "")).strip().lower() == expected_name:
            return asset
    return None


def get_repo_candidates_from_config() -> list[str]:
    repos: list[str] = []

    for env_name in _GITHUB_REPO_ENV_VARS:
        repos.extend(_split_repo_candidates(os.environ.get(env_name, "")))
    for env_name in _GITHUB_REPO_ALIAS_ENV_VARS:
        repos.extend(_split_repo_candidates(os.environ.get(env_name, "")))

    try:
        config_paths: list[Path] = []
        appdata = os.getenv("APPDATA")
        if appdata:
            config_paths.append(Path(appdata) / "Fretio" / "CONFIG.toml")
            config_paths.append(Path(appdata) / "FreteBot" / "CONFIG.toml")
        base = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
        config_paths.append(base / "CONFIG.toml")
        if base != Path(__file__).parent:
            config_paths.append(Path(__file__).parent / "CONFIG.toml")

        for cp in config_paths:
            if not cp.exists():
                continue
            cfg = _load_toml_file(cp)
            for section_name in _GITHUB_REPO_CONFIG_SECTIONS:
                section = cfg.get(section_name, {})
                if not isinstance(section, dict):
                    continue
                repos.extend(_split_repo_candidates(section.get("github_repo", "")))
                repos.extend(_split_repo_candidates(section.get("github_repo_aliases", [])))
    except Exception:
        pass

    repos.extend(_DEFAULT_GITHUB_REPOS)
    return _dedupe_repos(repos)


def get_repo_from_config() -> str:
    """Lê o primeiro repositório GitHub configurado para auto-update."""
    candidates = get_repo_candidates_from_config()
    return candidates[0] if candidates else _DEFAULT_GITHUB_REPOS[0]


def _resolve_repo_candidates(repo: str | Sequence[str] | None) -> list[str]:
    repos: list[str] = []
    if repo:
        repos.extend(_split_repo_candidates(repo))
    repos.extend(get_repo_candidates_from_config())
    return _dedupe_repos(repos)


def check_for_update(
    repo: str | Sequence[str] | None,
    current_version: str,
) -> Optional[UpdateInfo]:
    """
    Verifica se há uma versão mais nova no GitHub.

    Args:
        repo: "owner/repo" ou lista de aliases
        current_version: versão atual (ex: "1.21")

    Returns:
        UpdateInfo se houver atualização, None caso contrário.
    """
    repo_candidates = _resolve_repo_candidates(repo)
    if not repo_candidates:
        return None

    local_ver = _parse_version(current_version)

    for repo_name in repo_candidates:
        try:
            url = f"https://api.github.com/repos/{repo_name}/releases/latest"
            data = _github_api(url)
        except (URLError, OSError, json.JSONDecodeError):
            continue

        tag = data.get("tag_name", "")
        if not tag:
            continue

        remote_ver = _parse_version(tag)
        if remote_ver <= local_ver:
            continue

        assets = data.get("assets", [])
        zip_asset = _select_update_asset(assets)
        if not zip_asset:
            continue
        signature_asset = _select_signature_asset(assets, str(zip_asset.get("name", "")))

        return UpdateInfo(
            tag=tag,
            version=tag.lstrip("vV").strip(),
            download_url=zip_asset["browser_download_url"],
            asset_name=zip_asset["name"],
            asset_size=zip_asset.get("size", 0),
            release_notes=data.get("body", "") or "",
            html_url=data.get("html_url", ""),
            source_repo=repo_name,
            signature_download_url=(signature_asset or {}).get("browser_download_url", ""),
            signature_asset_name=(signature_asset or {}).get("name", ""),
        )
    return None


def _get_app_dir() -> Path:
    """Retorna o diretório raiz do app (onde está o exe ou script)."""
    if getattr(sys, "frozen", False):
        # PyInstaller one-folder: exe está em dist/Fretio/Fretio.exe
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


def _download_with_progress(
    url: str,
    dest: Path,
    total_size: int = 0,
    callback: Optional[Callable[[str], None]] = None,
) -> None:
    """Baixa arquivo com progresso."""
    req = Request(url, headers={"User-Agent": "Fretio-Updater/1.0"})
    with urlopen(req, timeout=120) as resp:
        downloaded = 0
        chunk_size = 64 * 1024  # 64 KB
        with open(dest, "wb") as f:
            while True:
                chunk = resp.read(chunk_size)
                if not chunk:
                    break
                f.write(chunk)
                downloaded += len(chunk)
                if callback and total_size > 0:
                    pct = min(100, int(downloaded / total_size * 100))
                    callback(f"Baixando atualização... {pct}%")


def _signature_download_url(info: UpdateInfo) -> str:
    if info.signature_download_url:
        return info.signature_download_url
    if info.download_url:
        return f"{info.download_url}.sig"
    return ""


def _signature_asset_name(info: UpdateInfo) -> str:
    if info.signature_asset_name:
        return info.signature_asset_name
    if info.asset_name:
        return f"{info.asset_name}.sig"
    return "update.zip.sig"


def _download_update_signature(info: UpdateInfo, dest: Path) -> None:
    signature_url = _signature_download_url(info)
    if not signature_url:
        raise ValueError("Assinatura do update ausente.")
    _download_with_progress(signature_url, dest, 0, None)


def apply_update(
    info: UpdateInfo,
    callback: Optional[Callable[[str], None]] = None,
) -> bool:
    """
    Baixa e aplica a atualização.

    Estratégia para Windows: como o exe e DLLs estão em uso e não podem
    ser sobrescritos diretamente, o updater:
    1. Baixa e extrai o ZIP para uma pasta temporária em %APPDATA%
    2. Cria um script .bat que:
       - Aguarda o app fechar
       - Copia os novos arquivos por cima dos antigos
       - Reinicia o app
    3. O app fecha e o script .bat assume

    Args:
        info: informações da atualização (de check_for_update)
        callback: função para reportar progresso

    Returns:
        True se a atualização foi preparada com sucesso.
    """
    app_dir = _get_app_dir()

    if callback:
        callback(f"Baixando v{info.version}...")

    # Pasta persistente para o update (não temp, senão é apagada)
    update_dir = _license_dir_update()
    update_dir.mkdir(parents=True, exist_ok=True)

    zip_path = update_dir / info.asset_name
    signature_path = update_dir / _signature_asset_name(info)

    try:
        _download_with_progress(info.download_url, zip_path, info.asset_size, callback)
        _download_update_signature(info, signature_path)
        verify_update_signature(zip_path, signature_path)

        if callback:
            callback("Extraindo atualização...")

        # Extrai para subpasta
        extract_dir = update_dir / "extracted"
        if extract_dir.exists():
            shutil.rmtree(str(extract_dir), ignore_errors=True)

        with zipfile.ZipFile(zip_path, "r") as zf:
            # Valida path traversal antes de extrair
            resolved_extract = extract_dir.resolve()
            for member in zf.namelist():
                member_path = (extract_dir / member).resolve()
                if os.path.commonpath([str(resolved_extract), str(member_path)]) != str(resolved_extract):
                    raise ValueError(f"Path traversal detectado no ZIP: {member!r}")
            zf.extractall(extract_dir)

        # Detecta raiz do conteúdo extraído
        contents = list(extract_dir.iterdir())
        if len(contents) == 1 and contents[0].is_dir():
            source_dir = contents[0]
        else:
            source_dir = extract_dir

        if callback:
            callback("Preparando atualização...")

        # Cria script batch que faz a substituição após o app fechar
        bat_path = update_dir / "_apply_update.bat"
        app_exe = Path(sys.executable) if getattr(sys, "frozen", False) else None
        pid = os.getpid()

        # Arquivos/pastas protegidos (não sobrescrever)
        bat_content = f'''@echo off
chcp 65001 >nul 2>&1
title Fretio - Atualizando...
echo Aguardando Fretio fechar...

:wait_loop
tasklist /FI "PID eq {pid}" 2>nul | find /I "{pid}" >nul
if %ERRORLEVEL% == 0 (
    timeout /t 1 /nobreak >nul
    goto wait_loop
)

echo Fretio fechou. Aplicando atualizacao v{info.version}...
timeout /t 2 /nobreak >nul

xcopy /E /Y /I /Q "{source_dir}" "{app_dir}" >nul 2>&1

REM Restaurar CONFIG.toml protegido (xcopy pode ter sobrescrito)
REM O CONFIG.toml do usuario fica em %APPDATA%, entao nao e afetado

REM Atualizar version.txt
echo {info.version}> "{app_dir}\\version.txt"
echo {info.version}> "{app_dir}\\_internal\\version.txt"

echo Atualizacao concluida! Reiniciando...
timeout /t 1 /nobreak >nul

REM Limpar arquivos de update
del /Q "{zip_path}" >nul 2>&1
rmdir /S /Q "{extract_dir}" >nul 2>&1

REM Reiniciar o app
'''
        if app_exe:
            bat_content += f'start "" "{app_exe}"\n'

        bat_content += 'del "%~f0" >nul 2>&1\n'
        bat_content += 'exit\n'

        bat_path.write_text(bat_content, encoding="mbcs")

        # Marca que há uma atualização pendente
        pending_file = update_dir / "_pending_update"
        pending_file.write_text(str(bat_path), encoding="utf-8")

        if callback:
            callback(f"Atualização v{info.version} preparada! Reiniciando...")

        return True

    except Exception as e:
        if callback:
            callback(f"Erro na atualização: {e}")
        # Limpa em caso de erro
        try:
            if zip_path.exists():
                zip_path.unlink()
        except Exception:
            pass
        try:
            if signature_path.exists():
                signature_path.unlink()
        except Exception:
            pass
        return False


def _license_dir_update() -> Path:
    """Diretório para armazenar updates temporários."""
    appdata = os.getenv("APPDATA")
    if appdata:
        d = Path(appdata) / "Fretio" / "update"
    else:
        d = Path.home() / ".Fretio" / "update"
    d.mkdir(parents=True, exist_ok=True)
    return d


def needs_restart() -> bool:
    """Verifica se há uma atualização pendente que precisa de restart."""
    update_dir = _license_dir_update()
    return (update_dir / "_pending_update").exists()


def restart_app() -> None:
    """
    Reinicia o aplicativo executando o script de atualização.
    O script aguarda o app fechar, copia os novos arquivos e reinicia.
    """
    update_dir = _license_dir_update()
    pending_file = update_dir / "_pending_update"

    if pending_file.exists():
        bat_path = pending_file.read_text(encoding="utf-8").strip()
        if Path(bat_path).exists():
            # Lança o script batch em processo separado (não é filho do app)
            # CREATE_NO_WINDOW: cria console oculto para cmd.exe rodar o .bat
            # CREATE_NEW_PROCESS_GROUP: garante que o processo sobrevive ao pai
            # NÃO usar DETACHED_PROCESS junto, pois bloqueia o console do cmd
            subprocess.Popen(
                ["cmd", "/c", bat_path],
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                | getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
                close_fds=True,
            )
        pending_file.unlink(missing_ok=True)

    # Fecha o app
    sys.exit(0)
