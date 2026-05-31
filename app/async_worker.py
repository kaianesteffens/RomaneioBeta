"""Helpers para executar coroutines fora da thread principal do Qt.

Playwright e providers async devem compartilhar um unico event loop em uma
thread dedicada por janela/sessao. Isso evita rodar automacao na thread do Qt e
tambem evita criar loops concorrentes desnecessarios a cada operacao.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import queue
import threading
from dataclasses import dataclass
from typing import Any, Callable

logger = logging.getLogger(__name__)


@dataclass
class _WorkItem:
    coro_factory: Callable[[], Any] | None
    future: concurrent.futures.Future | None = None
    cleanup_coro_factory: Callable[[], Any] | None = None
    stop_after: bool = False


class AsyncWorkerLoop:
    """Event loop asyncio persistente em uma unica thread worker."""

    def __init__(self, *, name: str = "FretioAsyncWorkerLoop"):
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queue: queue.Queue[_WorkItem] = queue.Queue()
        self._state_lock = threading.Lock()
        self._started = threading.Event()
        self._closed = threading.Event()
        self._closing = False
        self._current_task: asyncio.Task | None = None
        self._thread = threading.Thread(target=self._run, name=name, daemon=True)
        self._thread.start()
        self._started.wait(timeout=2)

    @property
    def is_closed(self) -> bool:
        loop = self._loop
        return self._closed.is_set() or loop is None or loop.is_closed()

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        self._started.set()
        try:
            while True:
                item = self._queue.get()
                if item.cleanup_coro_factory is not None:
                    self._run_cleanup(item.cleanup_coro_factory)
                if item.coro_factory is not None and item.future is not None:
                    self._run_work_item(item)
                if item.stop_after:
                    break
        finally:
            self._cancel_pending_tasks()
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:
                pass
            try:
                loop.run_until_complete(loop.shutdown_default_executor())
            except Exception:
                pass
            try:
                loop.close()
            except Exception:
                pass
            self._closed.set()

    def _run_work_item(self, item: _WorkItem) -> None:
        assert self._loop is not None
        future = item.future
        if future is None or future.cancelled():
            return
        task = self._loop.create_task(item.coro_factory())
        with self._state_lock:
            self._current_task = task
        try:
            result = self._loop.run_until_complete(task)
        except asyncio.CancelledError:
            future.cancel()
        except BaseException as exc:
            logger.exception("Async worker task failed")
            if not future.cancelled():
                future.set_exception(exc)
        else:
            if not future.cancelled():
                future.set_result(result)
        finally:
            with self._state_lock:
                if self._current_task is task:
                    self._current_task = None

    def _run_cleanup(self, cleanup_coro_factory: Callable[[], Any]) -> None:
        assert self._loop is not None
        try:
            self._loop.run_until_complete(cleanup_coro_factory())
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Async worker cleanup failed")

    def _cancel_pending_tasks(self) -> None:
        loop = self._loop
        if loop is None:
            return
        pending = [task for task in asyncio.all_tasks(loop) if not task.done()]
        for task in pending:
            task.cancel()
        if pending:
            try:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            except Exception:
                pass

    def submit(self, coro_factory: Callable[[], Any]) -> concurrent.futures.Future | None:
        """Agenda uma coroutine factory no loop worker."""
        with self._state_lock:
            if self._closing or self._closed.is_set() or self._loop is None or self._loop.is_closed():
                return None
            future: concurrent.futures.Future = concurrent.futures.Future()
            self._queue.put(_WorkItem(coro_factory=coro_factory, future=future))
            return future

    def shutdown(
        self,
        *,
        cleanup_coro_factory: Callable[[], Any] | None = None,
        timeout: float = 3.0,
        cancel_first: bool = True,
    ) -> None:
        """Executa cleanup e encerra o loop.

        A fila e serial por desenho. `cancel_first` cancela itens ainda nao
        iniciados e solicita cancelamento best-effort da coroutine ativa antes
        do cleanup. Providers devem fechar browsers em `cleanup()` para cobrir
        operacoes Playwright que nao aceitem cancelamento imediato.
        """
        with self._state_lock:
            if self._closing:
                thread = self._thread
            else:
                self._closing = True
                thread = self._thread
                if cancel_first:
                    self._cancel_current_task()
                    self._cancel_queued_futures()
                self._queue.put(
                    _WorkItem(
                        coro_factory=None,
                        cleanup_coro_factory=cleanup_coro_factory,
                        stop_after=True,
                    )
                )
        if self._closed.is_set():
            return
        if thread.is_alive():
            thread.join(timeout)

    def _cancel_current_task(self) -> None:
        task = self._current_task
        loop = self._loop
        if task is None or task.done() or loop is None or loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(task.cancel)
        except RuntimeError:
            pass

    def _cancel_queued_futures(self) -> None:
        drained: list[_WorkItem] = []
        while True:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                break
            if item.future is not None:
                item.future.cancel()
            if item.stop_after or item.cleanup_coro_factory is not None:
                drained.append(item)
        for item in drained:
            self._queue.put(item)


__all__ = ["AsyncWorkerLoop"]
