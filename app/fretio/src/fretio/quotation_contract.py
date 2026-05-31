"""Contrato padronizado para requisição e resposta de cotação."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import inspect
from typing import Any, Literal, cast


QuoteStatus = Literal["ok", "sem_cotacao", "erro", "desabilitada", "nao_atendido"]
ALLOWED_QUOTE_STATUS: tuple[QuoteStatus, ...] = (
    "ok",
    "sem_cotacao",
    "erro",
    "desabilitada",
    "nao_atendido",
)

_STATUS_ALIASES: dict[str, QuoteStatus] = {
    "error": "erro",
    "disabled": "desabilitada",
    "desativada": "desabilitada",
    "desabilitado": "desabilitada",
    "no_quote": "sem_cotacao",
    "sem_resultado": "sem_cotacao",
    "not_served": "nao_atendido",
    "not_supported": "nao_atendido",
}

_SENSITIVE_KEY_FRAGMENTS = (
    "senha",
    "password",
    "secret",
    "token",
    "authorization",
    "cookie",
    "cpf",
    "cnpj",
    "api_key",
    "apikey",
    "bearer",
)
_REDACTED = "***"


def normalize_quote_status(status: Any) -> QuoteStatus | None:
    normalized = str(status or "").strip().lower()
    if normalized in ALLOWED_QUOTE_STATUS:
        return cast(QuoteStatus, normalized)
    if normalized in _STATUS_ALIASES:
        return _STATUS_ALIASES[normalized]
    if normalized.startswith("erro"):
        return "erro"
    return None


def _coerce_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except Exception:
        return None


def _coerce_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except Exception:
        return None


def _is_sensitive_key(key: str) -> bool:
    normalized = str(key or "").strip().lower()
    return any(fragment in normalized for fragment in _SENSITIVE_KEY_FRAGMENTS)


def sanitize_raw_payload(value: Any) -> Any:
    if isinstance(value, Mapping):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            safe_key = str(key)
            sanitized[safe_key] = _REDACTED if _is_sensitive_key(safe_key) else sanitize_raw_payload(item)
        return sanitized
    if isinstance(value, list):
        return [sanitize_raw_payload(item) for item in value]
    if isinstance(value, tuple):
        return tuple(sanitize_raw_payload(item) for item in value)
    return value


@dataclass(slots=True)
class QuoteRequest:
    origem_cep: str
    destino_cep: str
    uf_destino: str
    cnpj_destinatario: str
    peso_total_kg: float
    valor_nf: float
    volumes: int
    cubagem_m3: float
    cubagens: list[dict[str, Any]]
    tipo_frete: str = ""
    metadata: dict[str, Any] | None = None

    def to_legacy_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "origem": self.origem_cep,
            "destino": self.destino_cep,
            "peso": self.peso_total_kg,
            "valor": self.valor_nf,
            "uf_destino": self.uf_destino,
            "volumes": self.volumes,
            "cubagem_m3": self.cubagem_m3,
            "cubagens": self.cubagens,
            "cnpj_destinatario": self.cnpj_destinatario,
        }
        if self.tipo_frete:
            kwargs["tipo_frete"] = self.tipo_frete
        if isinstance(self.metadata, Mapping):
            legacy_kwargs = self.metadata.get("legacy_kwargs")
            if isinstance(legacy_kwargs, Mapping):
                for key, value in legacy_kwargs.items():
                    safe_key = str(key)
                    if safe_key not in kwargs:
                        kwargs[safe_key] = value
        return kwargs


@dataclass(slots=True)
class QuoteResponse:
    provider: str
    status: QuoteStatus
    valor_frete: float | None = None
    prazo_dias: int | None = None
    detalhes: str | None = None
    duration_ms: int | None = None
    stage: str | None = None
    error_code: str | None = None
    raw: Any = None

    def __post_init__(self) -> None:
        self.provider = str(self.provider or "").strip()
        normalized = normalize_quote_status(self.status)
        if normalized is None:
            allowed = ", ".join(ALLOWED_QUOTE_STATUS)
            raise ValueError(f"status inválido: {self.status!r}. Permitidos: {allowed}")
        self.status = normalized
        if self.raw is not None:
            self.raw = sanitize_raw_payload(self.raw)

    @classmethod
    def ok(
        cls,
        *,
        provider: str,
        valor_frete: float,
        prazo_dias: int,
        detalhes: str | None = None,
        duration_ms: int | None = None,
        stage: str | None = None,
        raw: Any = None,
    ) -> "QuoteResponse":
        return cls(
            provider=provider,
            status="ok",
            valor_frete=float(valor_frete),
            prazo_dias=int(prazo_dias),
            detalhes=detalhes,
            duration_ms=duration_ms,
            stage=stage,
            raw=raw,
        )

    @classmethod
    def error(
        cls,
        *,
        provider: str,
        detalhes: str | None = None,
        duration_ms: int | None = None,
        stage: str | None = None,
        error_code: str | None = None,
        raw: Any = None,
    ) -> "QuoteResponse":
        return cls(
            provider=provider,
            status="erro",
            detalhes=detalhes,
            duration_ms=duration_ms,
            stage=stage,
            error_code=error_code,
            raw=raw,
        )

    @classmethod
    def disabled(
        cls,
        *,
        provider: str,
        detalhes: str | None = None,
        duration_ms: int | None = None,
        stage: str | None = None,
        error_code: str | None = None,
        raw: Any = None,
    ) -> "QuoteResponse":
        return cls(
            provider=provider,
            status="desabilitada",
            detalhes=detalhes,
            duration_ms=duration_ms,
            stage=stage,
            error_code=error_code,
            raw=raw,
        )

    @classmethod
    def no_quote(
        cls,
        *,
        provider: str,
        detalhes: str | None = None,
        duration_ms: int | None = None,
        stage: str | None = None,
        error_code: str | None = None,
        raw: Any = None,
        status: QuoteStatus = "sem_cotacao",
    ) -> "QuoteResponse":
        normalized = normalize_quote_status(status)
        if normalized not in {"sem_cotacao", "nao_atendido"}:
            raise ValueError("QuoteResponse.no_quote aceita apenas status sem_cotacao ou nao_atendido")
        return cls(
            provider=provider,
            status=normalized,
            detalhes=detalhes,
            duration_ms=duration_ms,
            stage=stage,
            error_code=error_code,
            raw=raw,
        )


def quote_request_from_legacy_kwargs(
    legacy_kwargs: Mapping[str, Any],
    *,
    uf_destino: str = "",
    cnpj_destinatario: str = "",
) -> QuoteRequest:
    data = dict(legacy_kwargs or {})

    cubagens_value = data.get("cubagens")
    if isinstance(cubagens_value, list):
        cubagens = [item for item in cubagens_value if isinstance(item, dict)]
    else:
        cubagens = []

    metadata: dict[str, Any] | None = None
    extra_keys = {
        str(key): value
        for key, value in data.items()
        if str(key)
        not in {
            "origem",
            "destino",
            "peso",
            "valor",
            "uf_destino",
            "volumes",
            "cubagem_m3",
            "cubagens",
            "cnpj_destinatario",
            "tipo_frete",
        }
    }
    if extra_keys:
        metadata = {"legacy_kwargs": extra_keys}

    return QuoteRequest(
        origem_cep=str(data.get("origem", "") or ""),
        destino_cep=str(data.get("destino", "") or ""),
        uf_destino=str(data.get("uf_destino", uf_destino) or ""),
        cnpj_destinatario=str(data.get("cnpj_destinatario", cnpj_destinatario) or ""),
        peso_total_kg=_coerce_float(data.get("peso", 0.0)) or 0.0,
        valor_nf=_coerce_float(data.get("valor", 0.0)) or 0.0,
        volumes=_coerce_int(data.get("volumes", 0)) or 0,
        cubagem_m3=_coerce_float(data.get("cubagem_m3", 0.0)) or 0.0,
        cubagens=cubagens,
        tipo_frete=str(data.get("tipo_frete", "") or ""),
        metadata=metadata,
    )


def cotacao_legada_to_quote_response(
    cotacao: Any | None,
    *,
    provider: str = "",
    duration_ms: int | None = None,
    stage: str | None = None,
    error_code: str | None = None,
    raw: Any = None,
) -> QuoteResponse:
    if cotacao is None:
        return QuoteResponse.no_quote(
            provider=provider,
            detalhes=None,
            duration_ms=duration_ms,
            stage=stage,
            error_code=error_code,
            raw=raw,
        )

    provider_name = str(getattr(cotacao, "transportadora", "") or provider or "")
    valor_frete = _coerce_float(getattr(cotacao, "valor_frete", None))
    prazo_dias = _coerce_int(getattr(cotacao, "prazo_dias", None))
    detalhes = getattr(cotacao, "restricoes", None)
    if detalhes is None:
        detalhes = getattr(cotacao, "detalhes", None)

    return QuoteResponse(
        provider=provider_name,
        status="ok",
        valor_frete=valor_frete,
        prazo_dias=prazo_dias,
        detalhes=str(detalhes) if detalhes is not None else None,
        duration_ms=duration_ms,
        stage=stage,
        error_code=error_code,
        raw=raw,
    )


def resultado_cotacao_to_quote_response(resultado: Any, *, raw: Any = None) -> QuoteResponse:
    raw_status = str(getattr(resultado, "status", "") or "").strip()
    status = normalize_quote_status(raw_status) or "erro"
    error_code = str(getattr(resultado, "error_code", "") or "").strip() or None
    normalized_raw_status = raw_status.lower()
    if error_code is None and raw_status and status == "erro" and normalized_raw_status != "erro":
        error_code = normalized_raw_status
    payload_raw = raw if raw is not None else getattr(resultado, "raw", None)
    return QuoteResponse(
        provider=str(getattr(resultado, "transportadora", "") or ""),
        status=status,
        valor_frete=_coerce_float(getattr(resultado, "valor_frete", None)),
        prazo_dias=_coerce_int(getattr(resultado, "prazo_dias", None)),
        detalhes=str(getattr(resultado, "detalhes", "") or "") or None,
        duration_ms=_coerce_int(getattr(resultado, "duration_ms", None)),
        stage=str(getattr(resultado, "stage", "") or "") or None,
        error_code=error_code,
        raw=payload_raw,
    )


def quote_response_to_resultado_cotacao(
    response: QuoteResponse,
    *,
    resultado_cls: type[Any] | None = None,
) -> Any:
    resultado_status = response.status
    if response.status == "erro":
        normalized_error_code = str(response.error_code or "").strip().lower()
        if normalized_error_code.startswith("erro"):
            resultado_status = normalized_error_code

    payload = {
        "transportadora": response.provider,
        "status": resultado_status,
        "valor_frete": response.valor_frete,
        "prazo_dias": response.prazo_dias,
        "detalhes": response.detalhes,
        "duration_ms": response.duration_ms,
        "stage": response.stage,
        "error_code": response.error_code,
        "raw": response.raw,
    }
    if resultado_cls is None:
        return payload
    try:
        signature = inspect.signature(resultado_cls)
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
            payload = {key: value for key, value in payload.items() if key in allowed_keys}
    except (TypeError, ValueError):
        pass
    return resultado_cls(**payload)
