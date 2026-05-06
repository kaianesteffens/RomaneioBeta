"""
Fretio Launcher — bootstrapper universal sem versão no nome.

Fluxo:
  1. Verifica %APPDATA%\\Fretio\\app\\ por instalação existente
  2. Consulta GitHub pela release mais recente
  3. Baixa e extrai o ZIP de atualização se necessário
  4. Lança Fretio.exe
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import zipfile
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

try:
    import tkinter as tk
    from tkinter import messagebox, ttk
    _HAS_TK = True
except ImportError:
    _HAS_TK = False

GITHUB_REPOS = ("kaianesteffens/RomaneioBeta-releases",)
_APPDATA_ROOT = Path(os.environ.get("APPDATA", Path.home()))
_LOCALAPPDATA_ROOT = Path(os.environ.get("LOCALAPPDATA", _APPDATA_ROOT))
APP_DIR_CANDIDATES = (
    _LOCALAPPDATA_ROOT / "Programs" / "Fretio",
    _LOCALAPPDATA_ROOT / "Programs" / "Romaneio Beta",
    _LOCALAPPDATA_ROOT / "Programs" / "FreteBot",
    _APPDATA_ROOT / "Fretio" / "app",
    _APPDATA_ROOT / "FreteBot" / "app",
)
APP_EXE_NAMES = ("Fretio.exe", "FreteBot.exe")
HTTP_TIMEOUT = 20
PREFERRED_UPDATE_ASSET_NAMES = (
    "fretio-update-latest.zip",
    "fretebot-update-latest.zip",
    "romaneiobeta-update-latest.zip",
)


# ── helpers ──────────────────────────────────────────────────────────────────

def _resolve_app_dir() -> Path:
    for app_dir in APP_DIR_CANDIDATES:
        for exe_name in APP_EXE_NAMES:
            if (app_dir / exe_name).exists():
                return app_dir
        if (app_dir / "version.txt").exists() or (app_dir / "_internal" / "version.txt").exists():
            return app_dir
    return APP_DIR_CANDIDATES[0]


def _resolve_app_exe(app_dir: Path) -> Path:
    for exe_name in APP_EXE_NAMES:
        candidate = app_dir / exe_name
        if candidate.exists():
            return candidate
    return app_dir / APP_EXE_NAMES[0]


def _local_version(app_dir: Path) -> str | None:
    version_candidates = [
        app_dir / "version.txt",
        app_dir / "_internal" / "version.txt",
    ]
    for p in version_candidates:
        if p.exists():
            return p.read_text(encoding="utf-8").strip()
    return None


def _parse_ver(tag: str) -> tuple[int, ...]:
    tag = tag.lstrip("vV").strip()
    parts: list[int] = []
    for part in tag.split("."):
        try:
            parts.append(int(part))
        except ValueError:
            break
    return tuple(parts) if parts else (0,)


def _split_repo_candidates(raw) -> list[str]:
    if isinstance(raw, str):
        normalized = raw.replace(";", ",").replace("\n", ",")
        items = [item.strip() for item in normalized.split(",")]
    elif isinstance(raw, (list, tuple, set)):
        items = [str(item).strip() for item in raw]
    else:
        return []
    return [item for item in items if item and "/" in item]


def _dedupe_repos(repos) -> list[str]:
    seen = set()
    ordered = []
    for repo in repos:
        lowered = repo.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        ordered.append(repo)
    return ordered


def _load_toml_candidates() -> list[Path]:
    app_dir = _resolve_app_dir()
    candidates = [
        _APPDATA_ROOT / "Fretio" / "CONFIG.toml",
        _APPDATA_ROOT / "FreteBot" / "CONFIG.toml",
        app_dir / "_internal" / "CONFIG.toml",
        app_dir / "CONFIG.toml",
        Path(sys.executable).resolve().parent / "CONFIG.toml",
    ]
    return candidates


def _load_repo_candidates() -> list[str]:
    repos = []
    for env_name in (
        "FRETIO_GITHUB_REPO",
        "FRETIO_GITHUB_REPO_ALIASES",
        "FRETEBOT_GITHUB_REPO",
        "FRETEBOT_GITHUB_REPO_ALIASES",
        "Fretio_GITHUB_REPO",
        "Fretio_GITHUB_REPO_ALIASES",
    ):
        repos.extend(_split_repo_candidates(os.environ.get(env_name, "")))

    for candidate in _load_toml_candidates():
        if not candidate.exists():
            continue
        try:
            raw = candidate.read_text(encoding="utf-8-sig")
            try:
                import tomllib
                cfg = tomllib.loads(raw)
            except ImportError:
                import toml
                cfg = toml.loads(raw)
        except Exception:
            continue
        for section_name in ("fretio", "fretebot", "romaneio"):
            section = cfg.get(section_name, {})
            if not isinstance(section, dict):
                continue
            repos.extend(_split_repo_candidates(section.get("github_repo", "")))
            repos.extend(_split_repo_candidates(section.get("github_repo_aliases", [])))

    repos.extend(GITHUB_REPOS)
    return _dedupe_repos([repo for repo in repos if "/" in repo])


def _fetch_latest(repo: str) -> dict | None:
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    req = Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "Fretio-Launcher/1.0",
    })
    with urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        return json.loads(resp.read())


def _select_zip_asset(assets) -> dict | None:
    zip_assets = [asset for asset in assets if asset["name"].lower().endswith(".zip")]
    if not zip_assets:
        return None

    def _rank(asset):
        lowered = asset["name"].lower()
        if lowered in PREFERRED_UPDATE_ASSET_NAMES:
            return (0, PREFERRED_UPDATE_ASSET_NAMES.index(lowered), lowered)
        if "update" in lowered:
            return (1, 0, lowered)
        if "fretio" in lowered or "fretebot" in lowered or "romaneio" in lowered:
            return (2, 0, lowered)
        return (3, 0, lowered)

    return min(zip_assets, key=_rank)


def _resolve_latest_release():
    for repo in _load_repo_candidates():
        try:
            release = _fetch_latest(repo)
        except (URLError, OSError, Exception):
            continue
        if release and release.get("tag_name"):
            return repo, release
    return None, None


def _download(url: str, dest: Path, total: int, status_cb, progress_cb) -> None:
    req = Request(url, headers={"User-Agent": "Fretio-Launcher/1.0"})
    downloaded = 0
    with urlopen(req, timeout=180) as resp, open(dest, "wb") as f:
        while True:
            chunk = resp.read(65536)
            if not chunk:
                break
            f.write(chunk)
            downloaded += len(chunk)
            if total > 0:
                pct = min(94, int(downloaded / total * 94))
                progress_cb(pct)
                status_cb(
                    f"Baixando... {downloaded/1048576:.1f} / {total/1048576:.1f} MB"
                )


def _launch_app(app_exe: Path) -> None:
    subprocess.Popen([str(app_exe)], cwd=str(app_exe.parent))


# ── UI ────────────────────────────────────────────────────────────────────────

class _Window(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Fretio")
        self.geometry("400x148")
        self.resizable(False, False)
        self.configure(bg="#1e1e2e")

        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"+{(sw - 400)//2}+{(sh - 148)//2}")

        self._lbl = tk.Label(
            self, text="Iniciando Fretio...",
            bg="#1e1e2e", fg="#cdd6f4", font=("Segoe UI", 11),
        )
        self._lbl.pack(pady=(22, 6))

        style = ttk.Style(self)
        style.theme_use("default")
        style.configure(
            "L.Horizontal.TProgressbar",
            troughcolor="#313244", background="#89b4fa",
        )
        self._pb = ttk.Progressbar(
            self, orient="horizontal", length=340,
            mode="indeterminate", style="L.Horizontal.TProgressbar",
        )
        self._pb.pack(pady=4)
        self._pb.start(10)

        self._sub = tk.Label(
            self, text="", bg="#1e1e2e", fg="#a6adc8",
            font=("Segoe UI", 8),
        )
        self._sub.pack(pady=6)

        self._closable = False
        self.protocol("WM_DELETE_WINDOW", self._guard_close)

    def _guard_close(self) -> None:
        if self._closable:
            self.destroy()

    def set_label(self, text: str) -> None:
        self.after(0, lambda: self._lbl.configure(text=text))

    def set_status(self, text: str) -> None:
        self.after(0, lambda: self._sub.configure(text=text))

    def set_progress(self, pct: float) -> None:
        def _apply():
            self._pb.stop()
            self._pb.configure(mode="determinate", value=pct, maximum=100)
        self.after(0, _apply)

    def close(self) -> None:
        self._closable = True
        self.after(0, self.destroy)

    def error(self, msg: str) -> None:
        self._closable = True
        self.after(0, lambda: messagebox.showerror("Romaneio – Erro", msg))
        self.after(200, self.destroy)


# ── core logic ────────────────────────────────────────────────────────────────

def _worker(win: "_Window | None") -> None:
    def label(t: str):
        if win:
            win.set_label(t)
        else:
            print(t)

    def status(t: str):
        if win:
            win.set_status(t)
        else:
            print(" ", t)

    def progress(p: float):
        if win:
            win.set_progress(p)

    try:
        app_dir = _resolve_app_dir()
        app_exe = _resolve_app_exe(app_dir)
        local_ver = _local_version(app_dir)
        label("Verificando atualizações...")
        status(f"Versão instalada: {local_ver or 'nenhuma'}")

        try:
            release_repo, release = _resolve_latest_release()
        except Exception:
            release_repo, release = None, None

        needs_dl = True
        zip_asset = None
        remote_ver = None

        if release:
            tag = release.get("tag_name", "")
            remote_ver = tag.lstrip("vV").strip()
            if local_ver and app_exe.exists():
                if _parse_ver(remote_ver) <= _parse_ver(local_ver):
                    needs_dl = False
            if needs_dl:
                zip_asset = _select_zip_asset(release.get("assets", []))
        elif app_exe.exists():
            needs_dl = False
        else:
            msg = (
                "Não foi possível conectar ao servidor e nenhuma versão local "
                "foi encontrada.\n\nVerifique sua conexão com a internet."
            )
            if win:
                win.error(msg)
            else:
                print("ERRO:", msg)
            return

        if needs_dl and zip_asset:
            ver_str = remote_ver or "?"
            source_label = f" ({release_repo})" if release_repo else ""
            label(f"Baixando Fretio v{ver_str}{source_label}...")
            progress(0)

            tmp = Path(os.environ.get("TEMP", app_dir.parent)) / "_romaneio_launcher.zip"
            _download(
                zip_asset["browser_download_url"],
                tmp,
                zip_asset.get("size", 0),
                status,
                progress,
            )

            label("Instalando...")
            status("Extraindo arquivos...")
            progress(96)

            app_dir.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(tmp, "r") as zf:
                zf.extractall(app_dir)

            try:
                tmp.unlink()
            except OSError:
                pass

            app_exe = _resolve_app_exe(app_dir)
            progress(100)
            status(f"v{ver_str} instalado com sucesso!")
        else:
            label("Fretio está atualizado!")
            status(f"Versão {local_ver} — abrindo...")

        time.sleep(0.7)
        label("Abrindo aplicativo...")

        if not app_exe.exists():
            raise FileNotFoundError(f"Fretio.exe/FreteBot.exe não encontrado em {app_dir}")

        _launch_app(app_exe)

        if win:
            win.close()

    except Exception as exc:
        msg = str(exc)
        if win:
            win.error(msg)
        else:
            print("ERRO:", msg)
            input("Pressione Enter para sair...")


def main() -> None:
    if _HAS_TK:
        win = _Window()
        threading.Thread(target=_worker, args=(win,), daemon=True).start()
        win.mainloop()
    else:
        _worker(None)


if __name__ == "__main__":
    main()
