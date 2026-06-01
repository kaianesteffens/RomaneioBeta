"""Carregamento e resolução de configuração de cotação."""

from __future__ import annotations

from pathlib import Path
from typing import Any
import os
import sys

from .common import *
from .validation import _cep, _resolver_cep_origem_cached

def _config_template_path() -> Path | None:
    base = _base_dir()
    candidates = [
        base / "Fretio" / "CONFIG.example.toml",
        base / "CONFIG.example.toml",
        Path.cwd() / "Fretio" / "CONFIG.example.toml",
        Path.cwd() / "CONFIG.example.toml",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _default_config_path() -> Path:
    appdata = os.getenv("APPDATA")
    if appdata:
        return Path(appdata) / "Fretio" / "CONFIG.toml"
    return _base_dir() / "CONFIG.toml"


def _criar_config_padrao() -> Path | None:
    destino = _default_config_path()
    if destino.exists():
        return destino
    try:
        destino.parent.mkdir(parents=True, exist_ok=True)
        template_path = _config_template_path()
        if template_path:
            destino.write_text(template_path.read_text(encoding="utf-8"), encoding="utf-8")
        else:
            destino.write_text(_CONFIG_FALLBACK, encoding="utf-8")
        _log_diag(f"CONFIG.toml criado em: {destino}")
        return destino
    except Exception as error:
        _log_diag(f"Falha ao criar CONFIG.toml em {destino}: {error}")
        return None


def _candidatos_config(config_path: Path | None = None) -> list[Path]:
    if config_path:
        return [config_path]

    base = _base_dir()
    candidates = [
        base / "Fretio" / "CONFIG.toml",
        base / "CONFIG.toml",
        Path.cwd() / "Fretio" / "CONFIG.toml",
        Path.cwd() / "CONFIG.toml",
    ]

    appdata = os.getenv("APPDATA")
    if appdata:
        candidates.append(Path(appdata) / "Fretio" / "CONFIG.toml")

    programdata = os.getenv("PROGRAMDATA")
    if programdata:
        candidates.append(Path(programdata) / "Fretio" / "CONFIG.toml")

    return candidates


def _empresa_from_config_path(config_path: Path | None) -> str:
    if not config_path:
        return "default"
    try:
        path = Path(config_path)
        if path.name.lower() == "config.toml" and path.parent.name:
            return path.parent.name
    except Exception:
        pass
    return "default"


def _aplicar_credenciais_seguras(config: dict[str, Any], empresa: str) -> dict[str, Any]:
    try:
        import secure_credentials

        secure_credentials.migrate_plaintext_credentials(config, empresa)
        return secure_credentials.overlay_secure_credentials(config, empresa)
    except Exception as error:
        _log_diag(f"Credenciais seguras indisponíveis; usando CONFIG.toml: {error}")
        return config


def _carregar_config(config_path: Path | None = None) -> dict[str, Any]:
    if config_path is None:
        manager = ConfigManager.get_instance("default")
        config = manager.load_config()
        if manager.get_loaded_path() is None:
            criado = _criar_config_padrao()
            if criado:
                config = manager.reload()
        return config if isinstance(config, dict) else {}

    if tomllib is None:
        _log_diag("tomllib indisponível; usando configuração vazia")
        return {}

    candidates = _candidatos_config(config_path=config_path)
    for cfg_path in candidates:
        if not cfg_path.exists():
            continue
        try:
            with cfg_path.open("rb") as file:
                data = tomllib.load(file)
                if isinstance(data, dict):
                    _log_diag(f"CONFIG carregado de: {cfg_path}")
                    return _aplicar_credenciais_seguras(data, _empresa_from_config_path(cfg_path))
        except Exception as error:
            _log_diag(f"Falha ao ler CONFIG em {cfg_path}: {error}")

    if config_path is None:
        criado = _criar_config_padrao()
        if criado:
            try:
                with criado.open("rb") as file:
                    data = tomllib.load(file)
                    if isinstance(data, dict):
                        return _aplicar_credenciais_seguras(data, _empresa_from_config_path(criado))
            except Exception as error:
                _log_diag(f"Falha ao ler CONFIG criado em {criado}: {error}")

    _log_diag("Nenhum CONFIG.toml encontrado; usando configuração vazia")
    return {}


def obter_cep_origem_default(config_path: Path | None = None) -> str:
    config = apply_safe_runtime_overrides(_carregar_config(config_path=config_path))
    if not isinstance(config, dict):
        return CEP_ORIGEM_PADRAO
    romaneio_cfg = config.get("romaneio", {})
    if not isinstance(romaneio_cfg, dict):
        return CEP_ORIGEM_PADRAO
    cep_cfg = _cep(str(romaneio_cfg.get("cep_origem", "") or ""))
    return cep_cfg or CEP_ORIGEM_PADRAO


def _resolver_cep_origem(config: dict[str, Any], cep_origem_informado: str) -> str:
    cep_informado = _cep(cep_origem_informado)

    romaneio_cfg = config.get("romaneio", {}) if isinstance(config, dict) else {}
    cep_romaneio = ""
    if isinstance(romaneio_cfg, dict):
        cep_romaneio = _cep(str(romaneio_cfg.get("cep_origem", "") or ""))

    transportadoras_cfg = config.get("transportadoras", {}) if isinstance(config, dict) else {}
    transportadora_ceps: list[str] = []
    if isinstance(transportadoras_cfg, dict):
        for nome in ("braspress", "bauer", "trd"):
            sec = transportadoras_cfg.get(nome, {})
            if isinstance(sec, dict):
                cep_sec = _cep(str(sec.get("cep_origem", "") or ""))
                transportadora_ceps.append(cep_sec)

    resolved = _resolver_cep_origem_cached(
        cep_informado,
        cep_romaneio,
        tuple(transportadora_ceps),
    )
    if cep_informado:
        return resolved
    if cep_romaneio and resolved == cep_romaneio:
        _log_diag(f"Usando CEP origem do romaneio: {cep_romaneio}")
        return resolved
    for nome, cep_sec in zip(("braspress", "bauer", "trd"), transportadora_ceps):
        if cep_sec and resolved == cep_sec:
            _log_diag(f"Usando CEP origem de transportadoras.{nome}: {cep_sec}")
            return resolved
    _log_diag(f"Usando CEP origem padrão fixo: {CEP_ORIGEM_PADRAO}")
    return resolved


def _dados_envio(extrator, pedidos: list[Any]) -> dict[str, Any]:
    if not pedidos:
        return {}

    grupos_caixa, caixas_complementares, total_boxes, total_volume, total_weight, total_valor = extrator._calcular_caixas_agrupadas(pedidos)

    local_entrega = pedidos[0].local_entrega or ""
    destino_cep = extrator.obter_cep_local_entrega(local_entrega)
    uf_destino = ""
    cidade_destino = ""
    try:
        if hasattr(extrator, "obter_uf_local_entrega"):
            uf_destino = str(extrator.obter_uf_local_entrega(local_entrega) or "").strip().upper()
    except Exception:
        uf_destino = ""
    try:
        if hasattr(extrator, "_extrair_componentes_local"):
            _rua, _cep, cidade_uf = extrator._extrair_componentes_local(local_entrega)
            match = re.search(r"(.+?)\s*/\s*([A-Za-z]{2})$", str(cidade_uf or "").strip())
            if match:
                cidade_destino = re.sub(r"\s+", " ", str(match.group(1) or "").strip())
                if not uf_destino:
                    uf_destino = str(match.group(2) or "").strip().upper()
    except Exception:
        cidade_destino = ""
    cnpj_destinatario = _digits(getattr(pedidos[0], "cnpj_cliente", ""))

    def _parse_dims_cm(dims_str: str) -> tuple[int, int, int]:
        parts = re.split(r"[xX×]", str(dims_str))
        if len(parts) < 3:
            return 0, 0, 0
        try:
            # PDF traz dimensões na ordem A×L×C (altura × largura × comprimento)
            a = int(float(re.sub(r"[^\d.,]", "", parts[0].strip()).replace(",", ".") or "0"))
            l = int(float(re.sub(r"[^\d.,]", "", parts[1].strip()).replace(",", ".") or "0"))
            c = int(float(re.sub(r"[^\d.,]", "", parts[2].strip()).replace(",", ".") or "0"))
            return a, l, c
        except (ValueError, IndexError):
            return 0, 0, 0

    # Cubagens reais usadas no romaneio (inclui caixas complementares).
    # Consolidamos por dimensão para enviar múltiplas linhas quando necessário.
    cubagens_map: dict[tuple[int, int, int], int] = {}

    def _add_cubagem(dims_str: str, quantidade: int = 1) -> None:
        a, l, c = _parse_dims_cm(dims_str)
        if a <= 0 or l <= 0 or c <= 0:
            return
        try:
            qtd = int(quantidade or 0)
        except Exception:
            return
        if qtd <= 0:
            return
        key = (a, l, c)
        cubagens_map[key] = cubagens_map.get(key, 0) + qtd

    # 1) Caixas completas realmente usadas
    if isinstance(grupos_caixa, dict):
        for info in grupos_caixa.values():
            if not isinstance(info, dict):
                continue
            calc = info.get("calculated", {}) if isinstance(info.get("calculated", {}), dict) else {}
            full_boxes = int(calc.get("full_boxes", 0) or 0)
            if full_boxes <= 0:
                continue
            d = str(info.get("dims", "") or "").strip()
            if d:
                _add_cubagem(d, full_boxes)

    # 2) Caixas complementares realmente usadas
    if caixas_complementares:
        for cx in caixas_complementares:
            if not isinstance(cx, dict):
                continue
            d = str(cx.get("dims", "") or "").strip()
            if d:
                qtd_comp = cx.get("quantidade", 0)
                _add_cubagem(d, int(qtd_comp or 0))

    altura_cm = 0
    largura_cm = 0
    comprimento_cm = 0
    for (a, l, c), _q in cubagens_map.items():
        if a * l * c > altura_cm * largura_cm * comprimento_cm:
            altura_cm, largura_cm, comprimento_cm = a, l, c

    cubagens = [
        {
            "quantidade": int(q),
            "altura_cm": int(a),
            "largura_cm": int(l),
            "comprimento_cm": int(c),
        }
        for (a, l, c), q in cubagens_map.items()
    ]

    descricoes_itens = []
    for p in pedidos:
        for item in p.itens:
            if item.produto:
                descricoes_itens.append(item.produto.strip())
            if item.descricao:
                descricoes_itens.append(item.descricao.strip())

    return {
        "destino_cep": _cep(destino_cep),
        "uf_destino": uf_destino,
        "cidade_destino": cidade_destino,
        "cnpj_destinatario": cnpj_destinatario,
        "peso": float(total_weight or 0.0),
        "valor": float(total_valor or 0.0),
        "volumes": int(total_boxes or 0),
        "cubagem_m3": float(total_volume or 0.0),
        "comprimento_cm": comprimento_cm,
        "largura_cm": largura_cm,
        "altura_cm": altura_cm,
        "cubagens": cubagens,
        "descricoes_itens": descricoes_itens,
    }



__all__ = [name for name in globals() if not name.startswith("__")]
