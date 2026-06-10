from __future__ import annotations

import sys
import concurrent.futures
import threading
from contextlib import nullcontext
from typing import Any, Callable

from PySide6.QtCore import QEvent, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication

from cotacao_transportadoras import (
    cotar_transportadoras_romaneio_colado,
    formatar_resultados_cotacao,
    CHROME_MISSING_USER_MESSAGE,
    CHROME_DOWNLOAD_URL,
)
from error_reporter import report_error
from ui.events import (
    UpdateResultEvent,
    UpdateFinishedEvent,
    StatusUpdateEvent,
    CotacaoProgressEvent,
    LoginStatusEvent,
    RastreioResultEvent,
    RastreioFinishedEvent,
)


class WorkerMixin:
    def _post_event_safe(self, event: QEvent) -> None:
        """Posta evento na fila da UI de forma segura (ignora se app já encerrou)."""
        if self._is_shutting_down():
            return
        try:
            inst = QApplication.instance()
            if inst is not None:
                inst.postEvent(self, event)
        except Exception:
            pass

    def _post_status(self, msg: str) -> None:
        self._post_event_safe(StatusUpdateEvent(str(msg or "")))

    def _post_login_status(self, nome: str, status: str) -> None:
        self._post_event_safe(LoginStatusEvent(str(nome or ""), str(status or "")))

    def _post_cotacao_progress(self, payload: dict[str, Any]) -> None:
        self._post_event_safe(CotacaoProgressEvent(payload or {}))

    def _post_rastreio_progress(self, indice: int, total: int, resultado: Any) -> None:
        self._post_event_safe(RastreioResultEvent(indice, total, resultado))

    def _mostrar_chrome_ausente(self) -> None:
        self._chrome_warning_label.setText(CHROME_MISSING_USER_MESSAGE)
        self._chrome_warning_frame.setVisible(True)
        self.label_info.setText(CHROME_MISSING_USER_MESSAGE)
        self.label_info.setStyleSheet("color: #b42318;")
        for dot in getattr(self, "_login_status_dots", {}).values():
            dot.set_status("fail")

    def _set_carrier_login_status(self, nome: str, status: str) -> None:
        dot = self._login_status_dots.get(nome)
        if dot is not None:
            dot.set_status(status)
        pair = self._home_carrier_info.get(nome)
        if pair is None:
            return
        cr_dot, cr_tag = pair
        color_map = {
            "ok": ("#3fb950", "online", "TagGreen"),
            "fail": ("#f85149", "erro", "TagRed"),
            "pending": ("#e3b341", "aguardando", "TagAmber"),
        }
        color, text, tag_obj = color_map.get(status, ("#768390", "—", "TagAmber"))
        cr_dot.setStyleSheet(f"border-radius:3px;background:{color};")
        cr_tag.setText(text)
        cr_tag.setObjectName(tag_obj)
        cr_tag.style().unpolish(cr_tag)
        cr_tag.style().polish(cr_tag)

    def _abrir_instalacao_chrome(self) -> None:
        QDesktopServices.openUrl(QUrl(CHROME_DOWNLOAD_URL))

    def _is_shutting_down(self) -> bool:
        return self._shutdown_started.is_set()

    def _start_daemon_worker(self, target) -> bool:
        if self._is_shutting_down():
            return False
        threading.Thread(target=target, daemon=True).start()
        return True

    def _run_sync_worker(
        self,
        target: Callable[[], Any],
        *,
        context: str,
        log_label: str,
        on_success: Callable[[Any], None] | None = None,
        ui_error_handler: Callable[[BaseException], None] | None = None,
    ) -> bool:
        def _worker():
            try:
                result = target()
                if on_success is not None and not self._is_shutting_down():
                    on_success(result)
            except Exception as exc:
                self._handle_async_worker_failure(
                    exc=exc,
                    context=context,
                    log_label=log_label,
                    ui_error_handler=ui_error_handler,
                )

        return self._start_daemon_worker(_worker)

    def _track_async_future(self, future: concurrent.futures.Future) -> concurrent.futures.Future:
        with self._async_futures_lock:
            self._async_futures.add(future)
        future.add_done_callback(self._discard_async_future)
        return future

    def _discard_async_future(self, future: concurrent.futures.Future) -> None:
        with self._async_futures_lock:
            self._async_futures.discard(future)

    def _submit_async_future(
        self,
        coro_factory: Callable[[], Any],
    ) -> concurrent.futures.Future | None:
        if self._is_shutting_down():
            return None
        future = self._async_loop.submit(coro_factory)
        if future is None:
            return None
        return self._track_async_future(future)

    def _cancel_pending_async_futures(self) -> None:
        with self._async_futures_lock:
            futures = list(self._async_futures)
        for future in futures:
            try:
                future.cancel()
            except Exception:
                pass

    def _handle_async_worker_failure(
        self,
        *,
        exc: BaseException,
        context: str,
        log_label: str,
        ui_error_handler: Callable[[BaseException], None] | None = None,
    ) -> None:
        report_error(*sys.exc_info(), context=context)
        print(
            (
                "[fretio] worker failed "
                f"context={context} label={log_label} "
                f"thread={threading.current_thread().name} "
                f"exc={type(exc).__name__}: {exc}"
            ),
            file=sys.stderr,
            flush=True,
        )
        if ui_error_handler is not None and not self._is_shutting_down():
            ui_error_handler(exc)

    def _run_async_worker(
        self,
        coro_factory: Callable[[], Any],
        *,
        context: str,
        log_label: str,
        sync_lock: Any | None = None,
        ui_error_handler: Callable[[BaseException], None] | None = None,
        on_success: Callable[[Any], None] | None = None,
    ) -> bool:
        def _worker():
            future: concurrent.futures.Future | None = None
            try:
                context_manager = sync_lock if sync_lock is not None else nullcontext()
                with context_manager:
                    future = self._submit_async_future(coro_factory)
                    if future is None:
                        return
                    result = future.result()
                if on_success is not None and not self._is_shutting_down():
                    on_success(result)
            except concurrent.futures.CancelledError:
                return
            except Exception as exc:
                self._handle_async_worker_failure(
                    exc=exc,
                    context=context,
                    log_label=log_label,
                    ui_error_handler=ui_error_handler,
                )

        return self._start_daemon_worker(_worker)

    def _run_pre_login(self):
        """Faz pre-login de todas as transportadoras em background."""
        if self._is_shutting_down():
            return
        self._run_async_worker(
            lambda: self._sessao.inicializar(
                callback=self._post_status,
                login_status_callback=self._post_login_status,
            ),
            context="pre_login",
            log_label="Erro no pre-login",
            sync_lock=self._session_task_lock,
            on_success=lambda _result: self._post_event_safe(StatusUpdateEvent(CHROME_MISSING_USER_MESSAGE))
            if self._sessao.chrome_missing else None,
        )

    def _run_async_cotacao(self):
        if self._is_shutting_down():
            return
        self._run_async_worker(
            self._cotar_transportadoras_async,
            context="run_async_cotacao",
            log_label="Erro na cotação",
            sync_lock=self._session_task_lock,
            ui_error_handler=lambda exc: (
                self._post_event_safe(UpdateResultEvent("Erro ao cotar transportadoras. Tente novamente em alguns minutos.")),
                self._post_event_safe(UpdateFinishedEvent()),
            ),
        )

    async def _cotar_transportadoras_async(self):
        try:
            if not self._sessao.pronto:
                self._post_status("Executando pre-login antes da cotação...")
                await self._sessao.inicializar(
                    callback=self._post_status,
                    login_status_callback=self._post_login_status,
                )
                self._pre_login_done = True
                if self._sessao.chrome_missing:
                    self._post_event_safe(UpdateResultEvent(CHROME_MISSING_USER_MESSAGE))
                    return

            _cotar_kwargs = dict(
                romaneio_colado=self._romaneio_colado,
                cep_origem=self._cep_origem_override,
                sessao=self._sessao,
                progresso_callback=self._post_cotacao_progress,
            )
            if getattr(self, "_modo_cotacao", "") == "fornecedor" and getattr(self, "_cnpj_fornecedor", ""):
                _cotar_kwargs["cnpj_remetente"] = self._cnpj_fornecedor
                _cotar_kwargs["tipo_frete"] = "2"  # FOB
            resultados = await cotar_transportadoras_romaneio_colado(**_cotar_kwargs)
            self._last_cotacao_results = resultados
            resumo = formatar_resultados_cotacao(resultados)

            # As atualizações da UI devem ser feitas na thread principal
            self._post_event_safe(UpdateResultEvent(resumo))

        except Exception as e:
            report_error(*sys.exc_info(), context="cotar_async")
            self._post_event_safe(UpdateResultEvent("Erro ao cotar transportadoras. Tente novamente em alguns minutos."))
        finally:
            self._post_event_safe(UpdateFinishedEvent())
