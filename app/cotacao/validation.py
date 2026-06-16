"""Validação e normalização de dados de cotação."""

from __future__ import annotations

from functools import lru_cache
from typing import Any
import re

from .common import CEP_ORIGEM_PADRAO

def _digits_cached(value: str) -> str:
    return re.sub(r"\D", "", value)


def _digits(value: Any) -> str:
    return _digits_cached(str(value or ""))


@lru_cache(maxsize=4096)
def _cep_cached(value: str) -> str:
    return _digits_cached(value)[:8]


def _cep(value: Any) -> str:
    return _cep_cached(str(value or ""))


# Mapeamento faixa de CEPs → UF (Correios)
_CEP_UF_FAIXAS: list[tuple[int, int, str]] = [
    (1000000, 19999999, "SP"),
    (20000000, 28999999, "RJ"),
    (29000000, 29999999, "ES"),
    (30000000, 39999999, "MG"),
    (40000000, 48999999, "BA"),
    (49000000, 49999999, "SE"),
    (50000000, 56999999, "PE"),
    (57000000, 57999999, "AL"),
    (58000000, 58999999, "PB"),
    (59000000, 59999999, "RN"),
    (60000000, 63999999, "CE"),
    (64000000, 64999999, "PI"),
    (65000000, 65999999, "MA"),
    (66000000, 68899999, "PA"),
    (68900000, 68999999, "AP"),
    (69000000, 69299999, "AM"),
    (69300000, 69399999, "RR"),
    (69400000, 69899999, "AM"),
    (69900000, 69999999, "AC"),
    (70000000, 72799999, "DF"),
    (72800000, 72999999, "GO"),
    (73000000, 73699999, "DF"),
    (73700000, 76799999, "GO"),
    (76800000, 76999999, "RO"),
    (77000000, 77999999, "TO"),
    (78000000, 78899999, "MT"),
    (78900000, 78999999, "MS"),
    (79000000, 79999999, "MS"),
    (80000000, 87999999, "PR"),
    (88000000, 89999999, "SC"),
    (90000000, 99999999, "RS"),
]


@lru_cache(maxsize=4096)
def _cep_para_uf_cached(cep_digits: str) -> str | None:
    """Retorna a UF correspondente a um CEP de 8 dígitos."""
    if len(cep_digits) != 8:
        return None
    try:
        cep_num = int(cep_digits)
    except ValueError:
        return None
    for inicio, fim, uf in _CEP_UF_FAIXAS:
        if inicio <= cep_num <= fim:
            return uf
    return None


def _cep_para_uf(cep: Any) -> str | None:
    return _cep_para_uf_cached(_cep(cep))


def _ufs_cache_key(ufs_config: list[str] | tuple[str, ...] | str | None) -> str | tuple[str, ...] | None:
    if ufs_config is None:
        return None
    if isinstance(ufs_config, str):
        return ufs_config
    return tuple(str(u or "") for u in ufs_config)


@lru_cache(maxsize=512)
def _normalizar_ufs_atendidas_cached(
    ufs_key: str | tuple[str, ...] | None,
) -> tuple[str, ...]:
    if not ufs_key:
        return ()
    if isinstance(ufs_key, str):
        values = ufs_key.split(",")
    else:
        values = ufs_key
    return tuple(str(u).strip().upper() for u in values if str(u).strip())


def _uf_atendida(ufs_config: list[str] | str | None, uf_destino: str | None) -> bool:
    """Verifica se a UF de destino está na lista de UFs atendidas."""
    if not ufs_config:
        return True  # sem filtro = atende tudo
    if not uf_destino:
        return True  # sem UF = tenta mesmo assim
    ufs_config = _normalizar_ufs_atendidas_cached(_ufs_cache_key(ufs_config))
    if not ufs_config:
        return True
    return uf_destino.upper() in ufs_config


@lru_cache(maxsize=512)
def _resolver_cep_origem_cached(
    cep_informado: str,
    cep_romaneio: str,
    transportadora_ceps: tuple[str, ...],
) -> str:
    if cep_informado:
        return cep_informado
    if cep_romaneio:
        return cep_romaneio
    for cep_sec in transportadora_ceps:
        if cep_sec:
            return cep_sec
    return CEP_ORIGEM_PADRAO


def _clear_validation_caches() -> None:
    _digits_cached.cache_clear()
    _cep_cached.cache_clear()
    _cep_para_uf_cached.cache_clear()
    _normalizar_ufs_atendidas_cached.cache_clear()
    _resolver_cep_origem_cached.cache_clear()


def _cubagens_validas(cubagens_raw: Any) -> list[dict[str, Any]]:
    validas: list[dict[str, Any]] = []
    if not isinstance(cubagens_raw, list):
        return validas
    for row in cubagens_raw:
        if not isinstance(row, dict):
            continue
        try:
            qtd = int(row.get("quantidade", 0) or 0)
            c = int(row.get("comprimento_cm", 0) or 0)
            l = int(row.get("largura_cm", 0) or 0)
            a = int(row.get("altura_cm", 0) or 0)
        except Exception:
            continue
        if qtd <= 0 or c <= 0 or l <= 0 or a <= 0:
            continue
        peso_por_volume_kg = None
        try:
            peso_raw = row.get("peso_por_volume_kg", None)
            if peso_raw is not None:
                peso_val = float(peso_raw)
                if peso_val > 0:
                    peso_por_volume_kg = peso_val
        except Exception:
            peso_por_volume_kg = None
        validas.append(
            {
                "quantidade": qtd,
                "comprimento_cm": c,
                "largura_cm": l,
                "altura_cm": a,
                "peso_por_volume_kg": peso_por_volume_kg,
            }
        )
    return validas


__all__ = [name for name in globals() if not name.startswith("__")]
