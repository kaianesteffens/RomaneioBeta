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

    O ZIP deve conter a estrutura do _internal/ do PyInstaller.
    Arquivos do ZIP substituem os existentes no diretório do app.
    CONFIG.toml do usuário NÃO é sobrescrito.

    Args:
        info: informações da atualização (de check_for_update)
        callback: função para reportar progresso

    Returns:
        True se a atualização foi aplicada com sucesso.
    """
    app_dir = _get_app_dir()
    internal_dir = app_dir / "_internal"

    if callback:
        callback(f"Baixando v{info.version}...")

    # Baixa ZIP para pasta temporária
    tmp_dir = Path(tempfile.mkdtemp(prefix="fretebot_update_"))
    zip_path = tmp_dir / info.asset_name

    try:
        _download_with_progress(info.download_url, zip_path, info.asset_size, callback)

        if callback:
            callback("Extraindo atualização...")

        # Extrai para subpasta temporária
        extract_dir = tmp_dir / "extracted"
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(extract_dir)

        # Detecta raiz do conteúdo extraído
        # O ZIP pode ter uma pasta raiz (ex: FreteBot/) ou não
        contents = list(extract_dir.iterdir())
        if len(contents) == 1 and contents[0].is_dir():
            source_dir = contents[0]
        else:
            source_dir = extract_dir

        if callback:
            callback("Aplicando atualização...")

        # Backup dos arquivos que serão substituídos
        backup_dir = tmp_dir / "backup"
        backup_dir.mkdir()

        # Arquivos/pastas protegidos (não sobrescrever)
        protected = {"CONFIG.toml", "cache", "crash.log"}

        # Copia novos arquivos para o diretório do app
        updated_count = 0
        for src_item in source_dir.rglob("*"):
            if not src_item.is_file():
                continue

            rel = src_item.relative_to(source_dir)

            # Protege arquivos de configuração do usuário
            if rel.parts[0] in protected or rel.name in protected:
                continue

            dest_file = app_dir / rel

            # Backup do arquivo existente
            if dest_file.exists():
                bkp = backup_dir / rel
                bkp.parent.mkdir(parents=True, exist_ok=True)
                try:
                    shutil.copy2(str(dest_file), str(bkp))
                except Exception:
                    pass

            # Copia novo arquivo
            dest_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(src_item), str(dest_file))
            updated_count += 1

        # Atualiza version.txt
        for vf in (app_dir / "version.txt", internal_dir / "version.txt"):
            try:
                vf.write_text(info.version, encoding="utf-8")
            except Exception:
                pass

        if callback:
            callback(f"Atualização v{info.version} aplicada! ({updated_count} arquivos)")

        return True

    except Exception as e:
        if callback:
            callback(f"Erro na atualização: {e}")
        return False

    finally:
        # Limpa arquivos temporários
        try:
            shutil.rmtree(str(tmp_dir), ignore_errors=True)
        except Exception:
            pass


def needs_restart() -> bool:
    """Verifica se o app precisa ser reiniciado após atualização (sempre True para PyInstaller)."""
    return getattr(sys, "frozen", False)


def restart_app() -> None:
    """Reinicia o aplicativo."""
    if getattr(sys, "frozen", False):
        os.execv(sys.executable, [sys.executable] + sys.argv[1:])
    else:
        os.execv(sys.executable, [sys.executable] + sys.argv)
