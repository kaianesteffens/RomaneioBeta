"""Golden for TRDProvider._aguardar_cep_cidade_entrega.

This poll loop (wait for the Angular portal to auto-complete CEP + cidade/UF of
the delivery address) lived inline as FOUR byte-identical copies inside
TRDProvider.cotear. Phase 6 step 4 collapsed them into one method; this pins the
extracted method's behavior (completes when the portal fills, times out when it
stays blank, swallows page errors as "not filled").
"""

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "app" / "fretio" / "src"))

from fretio.providers.trd import TRDProvider


class _Loc:
    def __init__(self, cep: str):
        self._cep = cep

    async def input_value(self):
        return self._cep


class _Page:
    def __init__(self, cep: str, cidade: str, *, evaluate_raises: bool = False):
        self._cep = cep
        self._cidade = cidade
        self._evaluate_raises = evaluate_raises
        self.waits = 0

    def locator(self, _sel):
        return _Loc(self._cep)

    async def evaluate(self, _js):
        if self._evaluate_raises:
            raise RuntimeError("page detached")
        return self._cidade

    async def wait_for_timeout(self, _ms):
        self.waits += 1


def _run(page):
    """Returns the full (cep_ok, cidade_uf_ok, cep_val, cidade_val) tuple."""
    provider = TRDProvider(email="a@b.com", senha="x")
    provider._page = page
    return asyncio.run(provider._aguardar_cep_cidade_entrega(tentativas=3, intervalo_ms=0))


def test_completes_when_portal_fills_cep_and_city():
    page = _Page("01001-000", "São Paulo / SP")
    cep_ok, cidade_uf_ok, cep_val, cidade_val = _run(page)
    assert cep_ok is True
    assert cidade_uf_ok is True
    assert page.waits == 0  # breaks on first iteration, no wait
    assert cep_val == "01001000"  # _digits strips the hyphen
    assert cidade_val == "São Paulo / SP"  # last-read value, for the diag log


def test_times_out_when_fields_stay_blank():
    page = _Page("", "")
    cep_ok, cidade_uf_ok, cep_val, cidade_val = _run(page)
    assert cep_ok is False
    assert cidade_uf_ok is False
    assert page.waits == 3  # waited once per attempt, never broke
    assert cep_val == ""
    assert cidade_val == ""


def test_cep_ok_but_city_missing_is_not_complete():
    page = _Page("01001000", "")  # valid 8-digit CEP, empty city
    cep_ok, cidade_uf_ok, _cep_val, cidade_val = _run(page)
    assert cep_ok is True
    assert cidade_uf_ok is False
    assert cidade_val == ""


def test_page_error_is_swallowed_as_not_filled():
    page = _Page("01001000", "São Paulo", evaluate_raises=True)
    cep_ok, cidade_uf_ok, cep_val, _cidade_val = _run(page)
    assert cep_ok is False
    assert cidade_uf_ok is False
    assert cep_val == ""  # exception path resets cep_val/cidade_val to ""
