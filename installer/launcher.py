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

GITHUB_REPO = "kaianesteffens/RomaneioBeta-releases"
APP_DIR = Path(os.environ.get("APPDATA", Path.home())) / "Fretio" / "app"
APP_EXE = APP_DIR / "Fretio.exe"
VERSION_CANDIDATES = [
    APP_DIR / "version.txt",
    APP_DIR / "_internal" / "version.txt",
]
HTTP_TIMEOUT = 20


# ── helpers ──────────────────────────────────────────────────────────────────

def _local_version() -> str | None:
    for p in VERSION_CANDIDATES:
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


def _fetch_latest() -> dict | None:
    url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
    req = Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "Fretio-Launcher/1.0",
    })
    with urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        return json.loads(resp.read())


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


def _launch_app() -> None:
    subprocess.Popen([str(APP_EXE)], cwd=str(APP_DIR))


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
        local_ver = _local_version()
        label("Verificando atualizações...")
        status(f"Versão instalada: {local_ver or 'nenhuma'}")

        try:
            release = _fetch_latest()
        except (URLError, OSError, Exception):
            release = None

        needs_dl = True
        zip_asset = None
        remote_ver = None

        if release:
            tag = release.get("tag_name", "")
            remote_ver = tag.lstrip("vV").strip()
            if local_ver and APP_EXE.exists():
                if _parse_ver(remote_ver) <= _parse_ver(local_ver):
                    needs_dl = False
            if needs_dl:
                assets = release.get("assets", [])
                zip_asset = next(
                    (a for a in assets if a["name"].lower().endswith(".zip")), None
                )
        elif APP_EXE.exists():
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
            label(f"Baixando Fretio v{ver_str}...")
            progress(0)

            tmp = Path(os.environ.get("TEMP", APP_DIR.parent)) / "_romaneio_launcher.zip"
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

            APP_DIR.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(tmp, "r") as zf:
                zf.extractall(APP_DIR)

            try:
                tmp.unlink()
            except OSError:
                pass

            progress(100)
            status(f"v{ver_str} instalado com sucesso!")
        else:
            label("Fretio está atualizado!")
            status(f"Versão {local_ver} — abrindo...")

        time.sleep(0.7)
        label("Abrindo aplicativo...")

        if not APP_EXE.exists():
            raise FileNotFoundError(f"Fretio.exe não encontrado em {APP_DIR}")

        _launch_app()

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
