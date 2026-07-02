import asyncio
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

from async_worker import AsyncWorkerLoop


def test_async_worker_runs_coroutines_off_main_thread():
    main_thread = threading.get_ident()
    worker = AsyncWorkerLoop(name="TestAsyncWorker")
    try:
        async def probe():
            return threading.get_ident(), id(asyncio.get_running_loop())

        first_thread, first_loop = worker.submit(probe).result(timeout=2)
        second_thread, second_loop = worker.submit(probe).result(timeout=2)

        assert first_thread != main_thread
        assert second_thread == first_thread
        assert second_loop == first_loop
    finally:
        worker.shutdown(timeout=2)


def test_async_worker_shutdown_runs_cleanup_and_rejects_new_work():
    worker = AsyncWorkerLoop(name="TestAsyncWorkerCleanup")
    cleanup_called = threading.Event()

    async def cleanup():
        cleanup_called.set()

    worker.shutdown(cleanup_coro_factory=cleanup, timeout=2)

    assert cleanup_called.is_set()
    assert worker.submit(lambda: asyncio.sleep(0)) is None


def test_async_worker_shutdown_cancels_queued_work_before_cleanup():
    worker = AsyncWorkerLoop(name="TestAsyncWorkerQueuedCancel")
    cleanup_called = threading.Event()

    async def first():
        await asyncio.sleep(0.2)

    async def second():
        return "should not run"

    async def cleanup():
        cleanup_called.set()

    first_future = worker.submit(first)
    second_future = worker.submit(second)
    assert first_future is not None
    assert second_future is not None

    worker.shutdown(cleanup_coro_factory=cleanup, timeout=2)

    assert cleanup_called.is_set()
    assert second_future.cancelled()
