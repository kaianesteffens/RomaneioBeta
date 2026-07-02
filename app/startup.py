"""Helpers de partida Qt-free (versão, config remota, migração de AppData).

Os diálogos de licença/atualização/versão-mínima que antes viviam aqui (PySide6)
foram migrados para a UI web (web_app.Api.startup_*). Este módulo agora contém
apenas lógica sem dependência de UI, reutilizada por web_app.py e app_bootstrap.py.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any


class MandatoryUpdateDeclined(RuntimeError):
    pass


def _resource_path(relative_path: str) -> Path:
    base = getattr(sys, '_MEIPASS', None)
    if base:
        return Path(base) / relative_path
    return Path(__file__).resolve().parent / relative_path


def _carregar_versao_app() -> str:
    candidatos = [
        _resource_path("version.txt"),
        Path(__file__).resolve().parent / "version.txt",
    ]
    for caminho in candidatos:
        try:
            if caminho.exists():
                versao = caminho.read_text(encoding="utf-8").strip()
                return versao
        except Exception:
            pass
    return "1.0.0"


def _migrate_appdata_fretebot_to_fretio() -> None:
    """Migra %APPDATA%\\FreteBot → %APPDATA%\\Fretio e remove o diretório antigo.

    Suporta dois casos:
    1. Fretio ainda não existe → move o diretório inteiro.
    2. Fretio já existe (criado em startup anterior) → faz merge não destrutivo
       das URLs de servidor ausentes no destino.
    """
    from company_config import (
        _escrever_config_toml,
        _garantir_defaults_empresa,
        _garantir_defaults_fretio,
    )

    appdata = os.getenv("APPDATA")
    if not appdata:
        return
    old_dir = Path(appdata) / "FreteBot"
    new_dir = Path(appdata) / "Fretio"
    if not old_dir.exists():
        return

    import shutil

    if not new_dir.exists():
        try:
            shutil.move(str(old_dir), str(new_dir))
        except Exception:
            pass
        return

    def _load_toml(path: Path) -> dict[str, Any]:
        try:
            raw = path.read_text(encoding="utf-8-sig")
        except Exception:
            return {}
        data = None
        try:
            import tomllib  # type: ignore[import]
            data = tomllib.loads(raw)
        except Exception:
            pass
        if data is None:
            try:
                import toml  # type: ignore[import-untyped]
                data = toml.loads(raw)
            except Exception:
                pass
        if data is None:
            try:
                import tomli as _tomli  # type: ignore[import-not-found]
                data = _tomli.loads(raw)
            except Exception:
                pass
        return data if isinstance(data, dict) else {}

    def _backfill_report_credentials(src_cfg: Path, dst_cfg: Path) -> None:
        try:
            src_data = _load_toml(src_cfg)
            dst_data = _load_toml(dst_cfg)

            _garantir_defaults_fretio(src_data)
            _garantir_defaults_empresa(src_data)
            dst_fb = dst_data.get("fretio", {}) if isinstance(dst_data.get("fretio", {}), dict) else {}
            defaults_changed = _garantir_defaults_fretio(dst_data)
            defaults_changed = _garantir_defaults_empresa(dst_data) or defaults_changed
            if defaults_changed:
                dst_fb = dst_data.get("fretio", {}) if isinstance(dst_data.get("fretio", {}), dict) else {}

            changed = False
            src_fb = src_data.get("fretio", {}) if isinstance(src_data.get("fretio", {}), dict) else {}
            for key in (
                "github_repo",
            ):
                src_val = str(src_fb.get(key, "") or "").strip()
                dst_val = str(dst_fb.get(key, "") or "").strip()
                if src_val and not dst_val:
                    if not isinstance(dst_data.get("fretio"), dict):
                        dst_data["fretio"] = {}
                    dst_data["fretio"][key] = src_val
                    changed = True

            if changed:
                _escrever_config_toml(dst_data, dst_cfg)
        except Exception:
            pass

    def _merge_missing(src: Path, dst: Path) -> None:
        if src.is_dir():
            if dst.exists() and not dst.is_dir():
                return
            dst.mkdir(parents=True, exist_ok=True)
            for child in sorted(src.iterdir(), key=lambda p: p.name.lower()):
                _merge_missing(child, dst / child.name)
            return

        if not src.is_file():
            return

        if not dst.exists():
            try:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(src), str(dst))
            except Exception:
                pass
            return

        if src.name.lower() == "config.toml":
            _backfill_report_credentials(src, dst)

    try:
        _merge_missing(old_dir, new_dir)
    except Exception:
        pass

    try:
        shutil.rmtree(str(old_dir), ignore_errors=True)
    except Exception:
        pass
