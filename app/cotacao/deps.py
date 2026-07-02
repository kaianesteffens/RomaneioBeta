"""Ponto único de injeção das dependências EXTERNAS da cotação.

Os submódulos chamam estas dependências como ``deps.<nome>(...)`` (acesso por
atributo) para que os testes possam substituí-las em um só lugar
(``monkeypatch.setattr(cotacao.deps, "<nome>", fake)``), sem o antigo
``_sync_legacy_overrides`` que copiava globais para dentro de cada submódulo a
cada cotação. Importa só de módulos externos ao pacote ``cotacao`` para não criar
import circular; helpers internos (report_provider_error, _carregar_config, ...)
continuam sendo importados normalmente e, nos testes, são substituídos no módulo
que os define.
"""
from __future__ import annotations

from fretio.providers.factory import ProviderFactory

try:
    from error_reporter import report_error, report_error_message, report_error_payload
except Exception:
    def report_error(*a, **kw):
        pass

    def report_error_message(*a, **kw):
        pass

    def report_error_payload(*a, **kw):
        pass

try:
    from remote_config import apply_safe_runtime_overrides
except Exception:
    def apply_safe_runtime_overrides(config):
        return dict(config) if isinstance(config, dict) else {}

try:
    from remote_permissions import carrier_enabled_or_message, normalize_carrier_name
except Exception:
    def carrier_enabled_or_message(carrier):
        return True, ""

    def normalize_carrier_name(carrier):
        return str(carrier or "").strip().lower()


__all__ = [
    "ProviderFactory",
    "apply_safe_runtime_overrides",
    "carrier_enabled_or_message",
    "normalize_carrier_name",
    "report_error",
    "report_error_message",
    "report_error_payload",
]
