"""
FreteBot Auto-Updater via GitHub Releases.

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
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.error import URLError
from urllib.request import Request, urlopen

# Timeout para requisições HTTP (segundos)
_HTTP_TIMEOUT = 30


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


def _github_api(url: str) -> Any:
    """Faz GET na API do GitHub e retorna JSON."""
    req = Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "FreteBot-Updater/1.0",
    })
    with urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
        return json.loads(resp.read())


def _parse_version(tag: str) -> tuple[int, ...]:
    """Converte '1.22' ou 'v1.22' em (1, 22) para comparação."""
    tag = tag.lstrip("vV").strip()
    parts: list[int] = []
    for p in tag.split("."):
        try:
            parts.append(int(p))
        except ValueError:
            break
    return tuple(parts) if parts else (0,)


def get_repo_from_config() -> str:
    """Lê o repositório GitHub do CONFIG ou retorna string vazia."""
    # Tenta ler de variável de ambiente primeiro
    repo = os.environ.get("FRETEBOT_GITHUB_REPO", "").strip()
    if repo:
        return repo

    # Tenta ler do CONFIG.toml
    try:
        import toml  # type: ignore[import-untyped]
        config_paths: list[Path] = []
        appdata = os.getenv("APPDATA")
        if appdata:
            config_paths.append(Path(appdata) / "FreteBot" / "CONFIG.toml")
        base = Path(getattr(sys, '_MEIPASS', Path(__file__).parent))
        config_paths.append(base / "CONFIG.toml")

        for cp in config_paths:
            if cp.exists():
                cfg = toml.load(cp)
                repo = cfg.get("fretebot", {}).get("github_repo", "")
                if repo:
                    return str(repo).strip()
    except Exception:
        pass
    return ""


def check_for_update(
    repo: str,
    current_version: str,
) -> Optional[UpdateInfo]:
    """
    Verifica se há uma versão mais nova no GitHub.

    Args:
        repo: "owner/repo" (ex: "meu-usuario/FreteBot")
        current_version: versão atual (ex: "1.21")

    Returns:
        UpdateInfo se houver atualização, None caso contrário.
    """
    if not repo or "/" not in repo:
        return None

    try:
        url = f"https://api.github.com/repos/{repo}/releases/latest"
        data = _github_api(url)
    except (URLError, OSError, json.JSONDecodeError):
        return None

    tag = data.get("tag_name", "")
    if not tag:
        return None

    remote_ver = _parse_version(tag)
    local_ver = _parse_version(current_version)

    if remote_ver <= local_ver:
        return None  # Já está atualizado

    # Procura asset ZIP para download
    assets = data.get("assets", [])
    zip_asset = None
    for asset in assets:
        name = asset.get("name", "")
        if name.lower().endswith(".zip"):
            zip_asset = asset
            break

    if not zip_asset:
        return None  # Release sem ZIP

    return UpdateInfo(
        tag=tag,
        version=tag.lstrip("vV").strip(),
        download_url=zip_asset["browser_download_url"],
        asset_name=zip_asset["name"],
        asset_size=zip_asset.get("size", 0),
        release_notes=data.get("body", "") or "",
        html_url=data.get("html_url", ""),
    )


def _get_app_dir() -> Path:
    """Retorna o diretório raiz do app (onde está o exe ou script)."""
    if getattr(sys, "frozen", False):
        # PyInstaller one-folder: exe está em dist/FreteBot/FreteBot.exe
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


def _download_with_progress(
    url: str,
    dest: Path,
    total_size: int = 0,
    callback: Optional[Callable[[str], None]] = None,
) -> None:
    """Baixa arquivo com progresso."""
    req = Request(url, headers={"User-Agent": "FreteBot-Updater/1.0"})
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

    try:
        _download_with_progress(info.download_url, zip_path, info.asset_size, callback)

        if callback:
            callback("Extraindo atualização...")

        # Extrai para subpasta
        extract_dir = update_dir / "extracted"
        if extract_dir.exists():
            shutil.rmtree(str(extract_dir), ignore_errors=True)

        with zipfile.ZipFile(zip_path, "r") as zf:
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
title FreteBot - Atualizando...
echo Aguardando FreteBot fechar...

:wait_loop
tasklist /FI "PID eq {pid}" 2>nul | find /I "{pid}" >nul
if %ERRORLEVEL% == 0 (
    timeout /t 1 /nobreak >nul
    goto wait_loop
)

echo FreteBot fechou. Aplicando atualizacao v{info.version}...
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
        return False


def _license_dir_update() -> Path:
    """Diretório para armazenar updates temporários."""
    appdata = os.getenv("APPDATA")
    if appdata:
        d = Path(appdata) / "FreteBot" / "update"
    else:
        d = Path.home() / ".fretebot" / "update"
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
