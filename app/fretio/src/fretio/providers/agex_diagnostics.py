"""Mixin de diagnóstico sanitizado do provider AGEX (métodos movidos de agex.py)."""
import os
import re
from fretio.logging_conf import get_logger

logger = get_logger(__name__)


class AGEXDiagnosticsMixin:
    @staticmethod
    def _safe_diagnostic_excerpt(value: object, *, limit: int = 900) -> str:
        text = str(value or "")
        text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", text)
        text = re.sub(r"(?is)<[^>]+>", " ", text)
        text = re.sub(r"(?<!\d)\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}(?!\d)", "***", text)
        text = re.sub(r"(?<!\d)\d{3}\.?\d{3}\.?\d{3}-?\d{2}(?!\d)", "***", text)
        text = re.sub(r"\b\d{14}\b", "***", text)
        text = re.sub(r"\b\d{11}\b", "***", text)
        text = re.sub(r"\b\d{5}-?\d{3}\b", "***", text)
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) > limit:
            return text[:limit].rstrip() + "..."
        return text

    async def _salvar_debug(self, sufixo: str) -> None:
        # Só grava diagnóstico quando explicitamente habilitado; nunca dump de
        # HTML/screenshot cru (contém CNPJ do pagador/destinatário e endereço).
        if not os.environ.get("FRETIO_DEBUG_DUMP"):
            return
        try:
            if self._page:
                debug_dir = os.path.join(os.environ.get("APPDATA", "."), "Fretio")
                os.makedirs(debug_dir, exist_ok=True)
                excerpt = self._safe_diagnostic_excerpt(await self._page.inner_text("body"))
                linha = f"url={self._page.url or ''}\n{excerpt}\n"
                with open(os.path.join(debug_dir, f"agex_{sufixo}.txt"), "w", encoding="utf-8") as f:
                    f.write(linha)
        except Exception as e:
            logger.warning(f"[{self.nome}] Falha ao salvar debug: {e}")
