"""Fase 7 — teardown determinístico ao fechar a janela web.

Trava o fix do vazamento (worker + sessão Playwright/Chrome do Rodonaves não eram
encerrados ao fechar), achado HIGH da auditoria.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "app" / "fretio" / "src"))

import web_app


class _FakeLoop:
    def __init__(self):
        self.shutdown_calls = []

    def shutdown(self, *, cleanup_coro_factory=None):
        self.shutdown_calls.append(cleanup_coro_factory)


class _FakeSessao:
    async def cleanup(self):
        return None


class _FakeEvent:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self


class _FakeEvents:
    def __init__(self):
        self.closed = _FakeEvent()


class _FakeWindow:
    def __init__(self):
        self.destroyed = False
        self.events = _FakeEvents()

    def destroy(self):
        self.destroyed = True


def _api():
    return web_app.Api(empresa="TESTE", config_path=ROOT / "nao_existe_config.toml")


def test_char_teardown_shuts_down_loop_with_session_cleanup():
    api = _api()
    loop, sessao = _FakeLoop(), _FakeSessao()
    api._loop, api._sessao = loop, sessao
    api._teardown()
    assert len(loop.shutdown_calls) == 1
    assert loop.shutdown_calls[0] == sessao.cleanup   # a coro de cleanup da sessão
    assert api._loop is None and api._sessao is None


def test_char_teardown_is_idempotent():
    api = _api()
    loop = _FakeLoop()
    api._loop, api._sessao = loop, _FakeSessao()
    api._teardown()
    api._teardown()  # segunda chamada não deve reencerrar
    assert len(loop.shutdown_calls) == 1


def test_char_teardown_without_session_passes_none():
    api = _api()
    loop = _FakeLoop()
    api._loop, api._sessao = loop, None
    api._teardown()
    assert loop.shutdown_calls == [None]


def test_char_sair_tears_down_then_destroys_window():
    api = _api()
    loop = _FakeLoop()
    api._loop, api._sessao = loop, _FakeSessao()
    win = _FakeWindow()
    api._window = win
    api.sair()
    assert len(loop.shutdown_calls) == 1
    assert win.destroyed is True


def test_char_attach_window_registers_closed_handler():
    api = _api()
    win = _FakeWindow()
    api.attach_window(win)
    # Fechar pelo X deve disparar o mesmo teardown.
    assert api._teardown in win.events.closed.handlers


def test_char_teardown_swallows_shutdown_errors():
    api = _api()

    class _BoomLoop:
        def shutdown(self, *, cleanup_coro_factory=None):
            raise RuntimeError("boom")

    api._loop, api._sessao = _BoomLoop(), _FakeSessao()
    api._teardown()  # não deve propagar
    assert api._loop is None and api._sessao is None
