from abc import ABC
import inspect
import re
import time
from typing import Any

from fretio.browser import (
    _ChromeBrowser,
    _cleanup_fallback_owned_procs,
    _find_free_port,
    _forget_owned_proc,
    _kill_pid_tree,
    _kill_proc,
    _register_owned_proc,
    browser_shutdown_requested,
    find_chrome,
    launch_browser_resilient,
    request_browser_shutdown,
)
from fretio.models import Cotacao
from fretio.quotation_contract import QuoteRequest, QuoteResponse, cotacao_legada_to_quote_response

_NAO_ATENDIDO_PATTERNS = (
    "rota não atendida",
    "rota nao atendida",
    "não atendido",
    "nao atendido",
    "não atendemos",
    "nao atendemos",
    "fora de cobertura",
    "destino fora da cobertura",
    "cep destino não atendido",
    "cepdestino não atendido",
    "cidade de destino",
    "sem precificação automática no ssw",
    "sem precificacao automatica no ssw",
)
_SEM_COTACAO_PATTERNS = (
    "sem cotação",
    "sem cotacao",
    "sem resultado",
    "sem valor de frete retornado",
    "portal respondeu sem cotação",
    "portal respondeu sem cotacao",
)
_TIMEOUT_PATTERNS = (
    "timeout",
    "timed out",
    "aguardando resultado",
)
_LOGIN_PATTERNS = (
    "login falhou",
    "erro no login",
    "credenciais",
    "acesso negado",
)
_DADOS_INVALIDOS_PATTERNS = (
    "cubagem inválida",
    "cubagem invalida",
    "cubagem do romaneio não encontrada",
    "cubagem do romaneio nao encontrada",
    "cubagens ausentes",
    "volumes inválidos",
    "volumes invalidos",
    "cnpj do destinatário inválido",
    "cnpj do destinatario invalido",
    "cep de destino inválido",
    "cep de destino invalido",
    "não informado",
    "nao informado",
)


class ProviderBase(ABC):
    def __init__(self, nome: str) -> None:
        self.nome = nome

    @staticmethod
    def _sanitize_quote_details(detail: str | None) -> str | None:
        if detail is None:
            return None
        text = str(detail).strip()
        if not text:
            return None
        text = re.sub(r"(?is)<[^>]+>", " ", text)
        text = re.sub(r"\s*\(diagnóstico salvo em:[^)]*\)", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\b\d{14}\b", "***", text)
        text = re.sub(r"\b\d{11}\b", "***", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text or None

    @staticmethod
    def _classify_no_quote_status(detail: str) -> tuple[str, str | None]:
        lowered = detail.lower()
        if any(pattern in lowered for pattern in _NAO_ATENDIDO_PATTERNS):
            return "nao_atendido", "nao_atendido"
        if any(pattern in lowered for pattern in _SEM_COTACAO_PATTERNS):
            return "sem_cotacao", "sem_cotacao"
        return "erro", None

    @staticmethod
    def _infer_error_code(detail: str | None, error: BaseException | None = None) -> str:
        lowered = str(detail or "").lower()
        if isinstance(error, TimeoutError) or any(pattern in lowered for pattern in _TIMEOUT_PATTERNS):
            return "timeout"
        if any(pattern in lowered for pattern in _LOGIN_PATTERNS):
            return "login_falhou"
        if any(pattern in lowered for pattern in _DADOS_INVALIDOS_PATTERNS):
            return "dados_invalidos"
        if "valor" in lowered and "não encontrado" in lowered:
            return "valor_nao_encontrado"
        if "valor" in lowered and "nao encontrado" in lowered:
            return "valor_nao_encontrado"
        return "falha_tecnica"

    @staticmethod
    def _quote_duration_ms(started_at: float) -> int:
        return max(0, int((time.monotonic() - started_at) * 1000))

    @staticmethod
    def _provider_stage(provider: "ProviderBase") -> str | None:
        stage = getattr(provider, "_passo_atual", None)
        if stage is None:
            return None
        normalized = str(stage).strip()
        return normalized or None

    def _legacy_kwargs_from_request(self, request: QuoteRequest) -> dict[str, Any]:
        legacy_kwargs = request.to_legacy_kwargs()
        try:
            signature = inspect.signature(self.coteir)
            has_var_kwargs = any(
                param.kind == inspect.Parameter.VAR_KEYWORD
                for param in signature.parameters.values()
            )
            if not has_var_kwargs:
                allowed_keys = {
                    name
                    for name, param in signature.parameters.items()
                    if param.kind in (
                        inspect.Parameter.POSITIONAL_OR_KEYWORD,
                        inspect.Parameter.KEYWORD_ONLY,
                    )
                }
                legacy_kwargs = {
                    key: value
                    for key, value in legacy_kwargs.items()
                    if key in allowed_keys
                }
        except (TypeError, ValueError):
            pass
        return legacy_kwargs

    def _quote_response_from_legacy_result(
        self,
        *,
        started_at: float,
        cotacao: Cotacao | None = None,
        error: BaseException | None = None,
        detalhes: str | None = None,
        stage: str | None = None,
    ) -> QuoteResponse:
        provider_name = str(self.nome or self.__class__.__name__).strip()
        duration_ms = self._quote_duration_ms(started_at)
        response_stage = stage or self._provider_stage(self)

        if cotacao is not None:
            return cotacao_legada_to_quote_response(
                cotacao,
                provider=provider_name,
                duration_ms=duration_ms,
                stage=response_stage,
            )

        base_detail = detalhes
        if base_detail is None and error is not None:
            base_detail = str(error)
        if base_detail is None:
            base_detail = getattr(self, "last_error", None)
        safe_detail = self._sanitize_quote_details(base_detail)

        if error is not None:
            error_code = self._infer_error_code(safe_detail, error)
            friendly = safe_detail or "Falha técnica na cotação"
            return QuoteResponse.error(
                provider=provider_name,
                detalhes=friendly,
                duration_ms=duration_ms,
                stage=response_stage,
                error_code=error_code,
            )

        if safe_detail is None:
            return QuoteResponse.no_quote(
                provider=provider_name,
                detalhes="Portal respondeu sem cotação",
                duration_ms=duration_ms,
                stage=response_stage,
                error_code="sem_cotacao",
            )

        status, status_code = self._classify_no_quote_status(safe_detail)
        if status in {"sem_cotacao", "nao_atendido"}:
            return QuoteResponse.no_quote(
                provider=provider_name,
                detalhes=safe_detail,
                duration_ms=duration_ms,
                stage=response_stage,
                error_code=status_code,
                status=status,
            )

        error_code = self._infer_error_code(safe_detail, error)
        return QuoteResponse.error(
            provider=provider_name,
            detalhes=safe_detail,
            duration_ms=duration_ms,
            stage=response_stage,
            error_code=error_code,
        )

    async def cotar(self, request: QuoteRequest) -> QuoteResponse:
        legacy_kwargs = self._legacy_kwargs_from_request(request)
        started_at = time.monotonic()
        try:
            cotacao = await self.coteir(**legacy_kwargs)
            return self._quote_response_from_legacy_result(
                started_at=started_at,
                cotacao=cotacao,
            )
        except Exception as exc:
            return self._quote_response_from_legacy_result(
                started_at=started_at,
                error=exc,
            )

    async def coteir(self, origem: str, destino: str, peso: float, valor: float) -> Cotacao | None:
        raise NotImplementedError(
            f"{self.__class__.__name__} deve implementar cotar(request) ou coteir(origem, destino, peso, valor)"
        )

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self.nome})"
