"""Regressão de segurança (CWE-312): dumps de diagnóstico dos providers (HTML
pós-login, payloads) só ocorrem com FRETIO_PROVIDER_DEBUG; em produção, nunca."""
import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "app" / "fretio" / "src"))
sys.path.insert(0, str(ROOT / "app"))

import fretio.providers.agex as agex_mod  # noqa: E402
import fretio.providers.trd as trd_mod  # noqa: E402


def _agex_with_page():
    p = object.__new__(agex_mod.AGEXProvider)
    p.nome = "agex"
    page = MagicMock()
    page.screenshot = AsyncMock()
    page.content = AsyncMock(return_value="<html>dados pos-login</html>")
    p._page = page
    return p, page


def test_agex_debug_dump_off_by_default(monkeypatch, tmp_path):
    monkeypatch.delenv("FRETIO_PROVIDER_DEBUG", raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path))
    p, page = _agex_with_page()
    asyncio.run(p._salvar_debug("pos_submit_etapa4"))
    page.screenshot.assert_not_called()
    page.content.assert_not_called()
    # nada gravado em disco
    assert not list((tmp_path / "Fretio").glob("*.html")) if (tmp_path / "Fretio").exists() else True


def test_agex_debug_dump_runs_when_enabled(monkeypatch, tmp_path):
    monkeypatch.setenv("FRETIO_PROVIDER_DEBUG", "1")
    monkeypatch.setenv("APPDATA", str(tmp_path))
    p, page = _agex_with_page()
    asyncio.run(p._salvar_debug("pos_submit_etapa4"))
    page.screenshot.assert_called_once()
    page.content.assert_called_once()


def test_trd_diagnostico_off_by_default(monkeypatch):
    monkeypatch.delenv("FRETIO_PROVIDER_DEBUG", raising=False)
    p = object.__new__(trd_mod.TRDProvider)
    page = MagicMock()
    page.screenshot = AsyncMock()
    page.content = AsyncMock(return_value="<html>form autenticado</html>")
    p._page = page
    out = asyncio.run(p._capturar_diagnostico_etapa2("falha_teste"))
    assert out == {}                      # nenhum caminho de arquivo retornado
    page.screenshot.assert_not_called()   # nada capturado
    page.content.assert_not_called()
