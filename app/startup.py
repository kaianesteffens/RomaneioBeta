from __future__ import annotations

import os
import queue
import sys
import threading
import time
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog, QInputDialog, QMessageBox

from company_config import (
    _escrever_config_toml,
    _garantir_defaults_empresa,
    _garantir_defaults_fretio,
)
from remote_config import fetch_remote_config, get_last_fetch_status
from updater import apply_update, check_for_update, restart_app
from usage_reporter import report_remote_config_fetched
from version_policy import evaluate_minimum_version, parse_semantic_version


class MandatoryUpdateDeclined(RuntimeError):
    pass


def _resource_path(relative_path: str) -> Path:
    base = getattr(sys, '_MEIPASS', None)
    if base:
        return Path(base) / relative_path
    return Path(__file__).resolve().parent / relative_path


def _fetch_remote_config_sync(startup_logger=None) -> dict[str, Any]:
    status = "error"
    payload: dict[str, Any] = {}
    try:
        fetched = fetch_remote_config(wait=True)
        status = get_last_fetch_status() or "default"
        if isinstance(fetched, dict):
            payload = fetched
        if startup_logger is not None:
            startup_logger.info("Configuracao remota carregada com status=%s", status)
    except Exception as exc:
        if startup_logger is not None:
            startup_logger.warning("Falha ao buscar configuracao remota: %s", exc)
        status = "error"
    try:
        report_remote_config_fetched(status=status)
    except Exception:
        pass
    return payload


def _run_startup_update_flow(
    repo: str,
    current_version: str,
    *,
    startup_logger=None,
    show_verification_error: bool = False,
) -> bool:
    if not repo:
        if show_verification_error:
            _show_startup_message(
                QMessageBox.Warning,
                "Atualizacao indisponivel",
                (
                    "Nao foi possivel verificar atualizacoes agora.\n"
                    "Confira sua conexao e tente novamente em alguns minutos."
                ),
            )
        return False

    try:
        update_info = check_for_update(repo, current_version)
    except Exception as exc:
        if startup_logger is not None:
            startup_logger.warning("Falha ao consultar update no repo %s: %s", repo, exc)
        if show_verification_error:
            _show_startup_message(
                QMessageBox.Warning,
                "Atualizacao indisponivel",
                (
                    "Nao foi possivel verificar atualizacoes agora.\n"
                    "Confira sua conexao e tente novamente em alguns minutos."
                ),
            )
        return False

    if not update_info:
        if show_verification_error:
            _show_startup_message(
                QMessageBox.Warning,
                "Atualizacao indisponivel",
                (
                    "Nao encontramos uma atualizacao automatica no momento.\n"
                    "Tente novamente mais tarde ou contate o suporte."
                ),
            )
        return False

    notes = str(update_info.release_notes or "").strip()
    message = (
        f"Nova versao encontrada: v{update_info.version}.\n\n"
        f"Versao atual: v{current_version}\n"
    )
    if update_info.mandatory:
        message += "\nEsta atualizacao e obrigatoria para continuar usando o sistema."
    else:
        message += "\nVoce pode atualizar agora ou continuar com esta versao."
    if notes:
        message += f"\n\nNotas da versao:\n{notes[:2000]}"

    prompt_icon = QMessageBox.Critical if update_info.mandatory else QMessageBox.Information
    prompt_title = "Atualizacao obrigatoria" if update_info.mandatory else "Atualizacao disponivel"
    dialog = QMessageBox(prompt_icon, prompt_title, message, QMessageBox.NoButton)
    btn_update = dialog.addButton("Atualizar agora", QMessageBox.AcceptRole)
    btn_close = None
    if update_info.mandatory:
        btn_close = dialog.addButton("Fechar aplicativo", QMessageBox.RejectRole)
    else:
        dialog.addButton("Continuar", QMessageBox.RejectRole)
    dialog.setWindowModality(Qt.ApplicationModal)
    dialog.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
    dialog.setWindowFlag(Qt.WindowStaysOnTopHint, True)
    dialog.exec()

    if dialog.clickedButton() is not btn_update:
        if update_info.mandatory and dialog.clickedButton() is btn_close:
            raise MandatoryUpdateDeclined("Atualizacao obrigatoria recusada pelo usuario.")
        return False

    progress = QMessageBox(
        QMessageBox.Information,
        "Atualizacao Automatica",
        (
            f"Nova versao encontrada: v{update_info.version}.\n"
            "Aplicando atualizacao automatica..."
        ),
        QMessageBox.NoButton,
    )
    progress.setWindowModality(Qt.ApplicationModal)
    progress.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
    progress.setWindowFlag(Qt.WindowStaysOnTopHint, True)
    progress.show()
    progress.raise_()
    progress.activateWindow()
    QApplication.processEvents()

    messages: queue.Queue[str] = queue.Queue()
    done = threading.Event()
    result: dict[str, Any] = {"ok": False, "exc": None}

    def _update_cb(message: str) -> None:
        messages.put(str(message or ""))

    def _apply_worker() -> None:
        try:
            result["ok"] = apply_update(update_info, callback=_update_cb)
        except Exception as exc:
            result["exc"] = exc
        finally:
            done.set()

    threading.Thread(target=_apply_worker, name="FretioStartupUpdate", daemon=True).start()

    while not done.is_set():
        try:
            while True:
                message = messages.get_nowait()
                if message:
                    progress.setText(message)
        except queue.Empty:
            pass
        QApplication.processEvents()
        time.sleep(0.05)
    try:
        while True:
            message = messages.get_nowait()
            if message:
                progress.setText(message)
    except queue.Empty:
        pass
    QApplication.processEvents()

    progress.close()
    if result["exc"] is not None:
        if startup_logger is not None:
            startup_logger.warning("Falha ao aplicar update: %s", result["exc"])
        ok = False
    else:
        ok = bool(result["ok"])

    if ok:
        _show_startup_message(
            QMessageBox.Information,
            "Atualizacao concluida",
            (
                f"Fretio foi atualizado para v{update_info.version}.\n"
                "O aplicativo vai reiniciar automaticamente."
            ),
        )
        restart_app()
        return True

    _show_startup_message(
        QMessageBox.Warning,
        "Atualizacao falhou",
        (
            "Nao foi possivel aplicar a atualizacao automatica agora.\n"
            "Voce pode tentar novamente em alguns minutos."
        ),
    )
    if update_info.mandatory:
        raise MandatoryUpdateDeclined("Atualizacao obrigatoria nao foi aplicada.")
    return False


def _enforce_minimum_version_policy(
    *,
    remote_payload: dict[str, Any] | None,
    current_version: str,
    repo: str,
    startup_logger=None,
) -> bool:
    config = (remote_payload or {}).get("config", {})
    policy = evaluate_minimum_version(config, current_version)
    if not policy.is_outdated:
        return True

    min_version = policy.min_app_version or "desconhecida"
    if startup_logger is not None:
        startup_logger.warning(
            "Versao abaixo do minimo remoto: atual=%s minimo=%s force_update=%s",
            policy.current_version,
            min_version,
            policy.force_update,
        )

    if policy.should_block:
        while True:
            dialog = QMessageBox(
                QMessageBox.Critical,
                "Atualizacao obrigatoria",
                (
                    "Sua versao do Fretio nao e compativel com o servidor.\n\n"
                    f"Versao atual: v{policy.current_version}\n"
                    f"Versao minima: v{min_version}\n\n"
                    "Atualize agora para continuar usando o sistema."
                ),
                QMessageBox.NoButton,
            )
            btn_update = dialog.addButton("Atualizar agora", QMessageBox.AcceptRole)
            btn_close = dialog.addButton("Fechar aplicativo", QMessageBox.RejectRole)
            dialog.setWindowModality(Qt.ApplicationModal)
            dialog.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
            dialog.setWindowFlag(Qt.WindowStaysOnTopHint, True)
            dialog.exec()

            if dialog.clickedButton() is btn_update:
                if _run_startup_update_flow(
                    repo,
                    current_version,
                    startup_logger=startup_logger,
                    show_verification_error=True,
                ):
                    return False
                continue

            if dialog.clickedButton() is btn_close:
                return False
            return False

    dialog = QMessageBox(
        QMessageBox.Warning,
        "Atualizacao recomendada",
        (
            "Existe uma versao minima recomendada para o sistema.\n\n"
            f"Versao atual: v{policy.current_version}\n"
            f"Versao minima: v{min_version}\n\n"
            "Voce pode atualizar agora ou continuar com esta versao."
        ),
        QMessageBox.NoButton,
    )
    btn_update = dialog.addButton("Atualizar agora", QMessageBox.AcceptRole)
    dialog.addButton("Continuar", QMessageBox.RejectRole)
    dialog.setWindowModality(Qt.ApplicationModal)
    dialog.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
    dialog.setWindowFlag(Qt.WindowStaysOnTopHint, True)
    dialog.exec()
    if dialog.clickedButton() is btn_update:
        _run_startup_update_flow(
            repo,
            current_version,
            startup_logger=startup_logger,
            show_verification_error=True,
        )
    return True


def _carregar_versao_app() -> str:
    candidatos = [
        _resource_path("version.txt"),
        Path(__file__).resolve().parent / "version.txt",
    ]
    for caminho in candidatos:
        try:
            if caminho.exists():
                versao = caminho.read_text(encoding="utf-8").strip()
                parse_semantic_version(versao)
                return versao
        except Exception:
            pass
    return "1.0.0"


def _show_startup_text_input(title: str, label: str, text: str = "") -> tuple[str, bool]:
    dialog = QInputDialog()
    dialog.setInputMode(QInputDialog.TextInput)
    dialog.setWindowTitle(title)
    dialog.setLabelText(label)
    dialog.setTextValue(text)
    dialog.setWindowModality(Qt.ApplicationModal)
    dialog.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
    dialog.setWindowFlag(Qt.WindowStaysOnTopHint, True)
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
    accepted = dialog.exec() == QDialog.Accepted
    return dialog.textValue(), accepted


def _show_startup_message(icon: QMessageBox.Icon, title: str, text: str) -> int:
    dialog = QMessageBox(icon, title, text, QMessageBox.Ok)
    dialog.setWindowModality(Qt.ApplicationModal)
    dialog.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
    dialog.setWindowFlag(Qt.WindowStaysOnTopHint, True)
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
    return dialog.exec()


def _migrate_appdata_fretebot_to_fretio() -> None:
    """Migra %APPDATA%\\FreteBot → %APPDATA%\\Fretio e remove o diretório antigo.

    Suporta dois casos:
    1. Fretio ainda não existe → move o diretório inteiro.
    2. Fretio já existe (criado em startup anterior) → faz merge não destrutivo
       e preserva credenciais de reporte (error_gist_id/error_report_token)
       quando presentes no legado e ausentes no destino.
    """
    appdata = os.getenv("APPDATA")
    if not appdata:
        return
    old_dir = Path(appdata) / "FreteBot"
    new_dir = Path(appdata) / "Fretio"
    if not old_dir.exists():
        return

    import shutil

    if not new_dir.exists():
        # Caso 1: diretório novo não existe → mover tudo de uma vez
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
        """Preenche defaults e credenciais ausentes no destino, sem sobrescrever valores já definidos."""
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
                "license_api_url",
                "license_config_api_url",
                "version_api_url",
                "license_url",
                "error_api_url",
                "usage_api_url",
                "quotation_jobs_api_url",
                "quotation_normalization_api_url",
                "error_gist_id",
                "error_report_token",
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

    # Caso 2: Fretio já existe → merge não destrutivo do conteúdo legado.
    try:
        _merge_missing(old_dir, new_dir)
    except Exception:
        pass

    # Remover diretório FreteBot após migração para evitar conflitos futuros
    try:
        shutil.rmtree(str(old_dir), ignore_errors=True)
    except Exception:
        pass
