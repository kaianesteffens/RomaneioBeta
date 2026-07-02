"""Mixin de diagnóstico sanitizado da etapa 2 do provider TRD (Senior X)."""
from __future__ import annotations
from datetime import datetime
from typing import Optional, Any
import json
import os
import re
import tempfile
from pathlib import Path


class TRDDiagnosticsMixin:
    """Coleta/sanitização de diagnóstico do TRDProvider."""

    def _safe_diagnostic_excerpt(self, value: Any, *, limit: int = 1500) -> str:
        """Excerpt textual sanitizado: remove tags, mascara CNPJ/CPF/CEP e corta."""
        text = str(value or "")
        text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", text)
        # reaproveita o mascaramento de documento do ProviderBase (trata separadores)
        text = self._sanitize_quote_details(text) or ""
        text = re.sub(r"\b\d{14}\b", "***", text)
        text = re.sub(r"\b\d{11}\b", "***", text)
        text = re.sub(r"\b\d{5}-?\d{3}\b", "***", text)
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) > limit:
            return text[:limit].rstrip() + "..."
        return text

    def _sanitize_extra_value(self, value: Any) -> Any:
        """Sanitiza recursivamente, preservando estrutura de dict/list; strings viram excerpt mascarado."""
        if isinstance(value, (int, float, bool)) or value is None:
            return value
        if isinstance(value, dict):
            return {str(k): self._sanitize_extra_value(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [self._sanitize_extra_value(v) for v in value]
        return self._safe_diagnostic_excerpt(value)

    async def _capturar_diagnostico_etapa2(
        self,
        motivo: str,
        extra_data: Optional[dict[str, Any]] = None,
    ) -> dict[str, str]:
        """Grava um excerpt textual sanitizado para diagnosticar falhas na etapa 2.

        Gated por FRETIO_DEBUG_DUMP: sem a env var, nada é gravado em disco. Mesmo
        ativo, não persiste screenshot/HTML/payloads de rede crus — só um resumo
        textual com CNPJ/CPF/CEP mascarados e tamanho reduzido.
        """
        paths: dict[str, str] = {}
        if not self._page:
            return paths
        if not os.environ.get("FRETIO_DEBUG_DUMP"):
            return paths

        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        base_dir = Path(tempfile.gettempdir()) / "fretio_trd_debug"
        try:
            base_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            return paths

        json_path = base_dir / f"{ts}_{motivo}.json"

        try:
            alerts = await self._coletar_alertas_ui()
        except Exception:
            alerts = []
        alerts_safe = [self._safe_diagnostic_excerpt(a, limit=300) for a in alerts]

        extra_safe = {
            str(key): self._sanitize_extra_value(value)
            for key, value in (extra_data or {}).items()
        }

        try:
            payload = {
                "motivo": motivo,
                "url": self._safe_diagnostic_excerpt(self._page.url, limit=240),
                "timestamp": ts,
                "alerts": alerts_safe,
                "extra": extra_safe,
            }
            json_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            paths["json"] = str(json_path)
        except Exception:
            pass

        paths["dir"] = str(base_dir)
        return paths
