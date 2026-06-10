"""Stubs de módulos de browser para testes de funções puras.

Instala substitutos mínimos de playwright antes que qualquer módulo seja
importado pelo pytest. Garante que testes em tests/ que cobrem lógica pura
(validation, classification, contract) funcionem em ambientes sem Playwright.

Usa setdefault — se playwright estiver instalado, os módulos reais prevalecem.
"""
import sys
import types


def _make_playwright_stub(name: str) -> types.ModuleType:
    mod = types.ModuleType(name)

    class TimeoutError(Exception):  # noqa: N818
        pass

    mod.TimeoutError = TimeoutError
    mod.async_playwright = None
    mod.Page = object
    return mod


for _stub_name in ("playwright", "playwright.async_api", "playwright.sync_api"):
    sys.modules.setdefault(_stub_name, _make_playwright_stub(_stub_name))
