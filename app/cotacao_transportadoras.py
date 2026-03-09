"""Cotação de transportadoras para integração com romaneio."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
import asyncio
from datetime import datetime
import os
import time
import re
import sys

# Error reporting remoto
try:
    from error_reporter import report_error, report_error_message
except Exception:
    def report_error(*a, **kw): pass
    def report_error_message(*a, **kw): pass

# Inicializar logging dos providers
try:
    from fretebot.logging_conf import setup_logging
    setup_logging()
except Exception:
    pass

try:
    import tomllib  # py311+
except Exception:  # pragma: no cover
    tomllib = None

# Adiciona a pasta 'src' ao sys.path para encontrar os módulos do fretebot
def _add_fretebot_src_to_path() -> None:
    repo_root = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
    src = repo_root / "fretebot" / "src"
    if src.exists() and str(src) not in sys.path:
        sys.path.insert(0, str(src))
_add_fretebot_src_to_path()


# Imports lazy — carregados sob demanda na primeira inicialização para
# não atrasar a abertura da janela (cada provider puxa playwright, etc.).
BraspressProvider = None
BauerAutoProvider = None
TRDProvider = None
AGEXProvider = None
EucaturProvider = None
RodonavesProvider = None
AlfaProvider = None
CoopexProvider = None

def _ensure_provider_imports() -> None:
    """Importa os providers na primeira chamada (lazy)."""
    global BraspressProvider, BauerAutoProvider, TRDProvider
    global AGEXProvider, EucaturProvider, RodonavesProvider, AlfaProvider, CoopexProvider
    if BraspressProvider is not None:
        return  # já carregado
    from fretebot.providers.braspress_playwright import BraspressPlaywrightProvider as _BP
    from fretebot.providers.bauer_auto import BauerAutoProvider as _BA
    from fretebot.providers.trd import TRDProvider as _TRD
    BraspressProvider = _BP
    BauerAutoProvider = _BA
    TRDProvider = _TRD
    try:
        from fretebot.providers.agex import AGEXProvider as _AG
    except ImportError:
        _AG = None
    AGEXProvider = _AG
    try:
        from fretebot.providers.eucatur import EucaturProvider as _EU
    except ImportError:
        _EU = None
    EucaturProvider = _EU
    try:
        from fretebot.providers.rodonaves import RodonavesProvider as _RO
    except ImportError:
        _RO = None
    RodonavesProvider = _RO
    try:
        from fretebot.providers.alfa import AlfaProvider as _AL
    except ImportError:
        _AL = None
    AlfaProvider = _AL
    try:
        from fretebot.providers.coopex import CoopexProvider as _CO
    except ImportError:
        _CO = None
    CoopexProvider = _CO


CEP_ORIGEM_PADRAO = "99740000"
MODO_FOCO_TRANSPORTADORA = ""  # Vazio = sem foco; cota todas as transportadoras habilitadas.
_CONFIG_FALLBACK = """[fretebot]
fator_cubagem = 6000
cache_dir = "cache"

[romaneio]
cep_origem = "99740000"

[transportadoras.braspress]
habilitado = true
cnpj = ""
senha = ""
ufs_atendidas = ["AC", "AL", "AM", "AP", "BA", "CE", "DF", "ES", "GO", "MA", "MG", "MS", "MT", "PA", "PB", "PE", "PI", "PR", "RJ", "RN", "RO", "RR", "RS", "SC", "SE", "SP", "TO"]

[transportadoras.bauer]
habilitado = true
cotacao_url = ""
cnpj_pagador = ""
cnpj_remetente = ""
cnpj_destinatario = ""
headless = true
quantidade = 1
ufs_atendidas = ["PR", "RS", "SC"]

[transportadoras.trd]
habilitado = true
email = ""
senha = ""
headless = true
volumes = 1
altura = 0.1
largura = 0.1
comprimento = 0.1
ufs_atendidas = ["RS", "SC", "PR", "SP", "MG", "ES", "RJ"]

[transportadoras.agex]
habilitado = false
cnpj = ""
senha = ""
cnpj_remetente = ""
cnpj_destinatario = ""
ufs_atendidas = ["PR", "SP", "GO", "DF", "TO", "PA", "MT", "MS"]

[transportadoras.eucatur]
habilitado = false
dominio = ""
usuario = ""
senha = ""
ufs_atendidas = ["RR", "AM", "AC", "RO", "MT", "MS"]

[transportadoras.rodonaves]
habilitado = false
dominio = "RTE"
usuario = ""
senha = ""
cnpj_pagador = ""
login_url = "https://cliente.rte.com.br/?showLogin=true"
cotacao_url = "https://sistema.rte.com.br/bin/ssw1608"
headless = true
ufs_atendidas = ["AC", "AL", "AM", "AP", "BA", "CE", "DF", "ES", "GO", "MA", "MG", "MS", "MT", "PA", "PB", "PE", "PI", "PR", "RJ", "RN", "RO", "RR", "RS", "SC", "SE", "SP", "TO"]

[transportadoras.alfa]
habilitado = false
login = ""
senha = ""
cnpj_remetente = ""
login_url = "https://arearestrita.alfatransportes.com.br/login/"
cotacao_url = "https://arearestrita.alfatransportes.com.br/cotacao/api/"
headless = false
ufs_atendidas = ["AC", "AL", "AM", "AP", "BA", "CE", "DF", "ES", "GO", "MA", "MG", "MS", "MT", "PA", "PB", "PE", "PI", "PR", "RJ", "RN", "RO", "RR", "RS", "SC", "SE", "SP", "TO"]

[transportadoras.coopex]
habilitado = false
dominio = ""
usuario = ""
senha = ""
ufs_atendidas = []
"""


@dataclass
class ResultadoCotacao:
    transportadora: str
    status: str
    valor_frete: float | None = None
    prazo_dias: int | None = None
    detalhes: str | None = None


def _digits(value: str) -> str:
    return re.sub(r"\D", "", value or "")


def _cep(value: str) -> str:
    digits = _digits(value)
    return digits[:8]


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


def _cep_para_uf(cep: str) -> str | None:
    """Retorna a UF correspondente a um CEP de 8 dígitos."""
    digits = _digits(cep)
    if len(digits) != 8:
        return None
    try:
        cep_num = int(digits)
    except ValueError:
        return None
    for inicio, fim, uf in _CEP_UF_FAIXAS:
        if inicio <= cep_num <= fim:
            return uf
    return None


def _uf_atendida(ufs_config: list[str] | str | None, uf_destino: str | None) -> bool:
    """Verifica se a UF de destino está na lista de UFs atendidas."""
    if not ufs_config:
        return True  # sem filtro = atende tudo
    if not uf_destino:
        return True  # sem UF = tenta mesmo assim
    if isinstance(ufs_config, str):
        ufs_config = [u.strip().upper() for u in ufs_config.split(",") if u.strip()]
    else:
        ufs_config = [u.strip().upper() for u in ufs_config if u.strip()]
    if not ufs_config:
        return True
    return uf_destino.upper() in ufs_config


def _base_dir() -> Path:
    return Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent


def _diag_log_enabled() -> bool:
    return not bool(getattr(sys, "frozen", False))


def _trd_headless_config_value(tcfg: dict[str, Any], foco_trd: bool) -> bool:
    """TRD sempre roda headless (invisível)."""
    return True


def _log_path() -> Path:
    appdata = os.getenv("APPDATA")
    if appdata:
        log_dir = Path(appdata) / "FreteBot"
        log_dir.mkdir(parents=True, exist_ok=True)
        return log_dir / "romaneio_cotacao.log"
    return _base_dir() / "romaneio_cotacao.log"


def _log_diag(msg: str) -> None:
    if not _diag_log_enabled():
        return
    try:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with _log_path().open("a", encoding="utf-8") as f:
            f.write(f"[{ts}] {msg}\n")
    except Exception:
        pass


def _config_template_path() -> Path | None:
    base = _base_dir()
    candidates = [
        base / "fretebot" / "CONFIG.example.toml",
        base / "CONFIG.example.toml",
        Path.cwd() / "fretebot" / "CONFIG.example.toml",
        Path.cwd() / "CONFIG.example.toml",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _default_config_path() -> Path:
    appdata = os.getenv("APPDATA")
    if appdata:
        return Path(appdata) / "FreteBot" / "CONFIG.toml"
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
        base / "fretebot" / "CONFIG.toml",
        base / "CONFIG.toml",
        Path.cwd() / "fretebot" / "CONFIG.toml",
        Path.cwd() / "CONFIG.toml",
    ]

    appdata = os.getenv("APPDATA")
    if appdata:
        candidates.append(Path(appdata) / "FreteBot" / "CONFIG.toml")

    programdata = os.getenv("PROGRAMDATA")
    if programdata:
        candidates.append(Path(programdata) / "FreteBot" / "CONFIG.toml")

    return candidates


def _carregar_config(config_path: Path | None = None) -> dict[str, Any]:
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
                    return data
        except Exception as error:
            _log_diag(f"Falha ao ler CONFIG em {cfg_path}: {error}")

    if config_path is None:
        criado = _criar_config_padrao()
        if criado:
            try:
                with criado.open("rb") as file:
                    data = tomllib.load(file)
                    if isinstance(data, dict):
                        return data
            except Exception as error:
                _log_diag(f"Falha ao ler CONFIG criado em {criado}: {error}")

    _log_diag("Nenhum CONFIG.toml encontrado; usando configuração vazia")
    return {}


def obter_cep_origem_default(config_path: Path | None = None) -> str:
    config = _carregar_config(config_path=config_path)
    if not isinstance(config, dict):
        return CEP_ORIGEM_PADRAO
    romaneio_cfg = config.get("romaneio", {})
    if not isinstance(romaneio_cfg, dict):
        return CEP_ORIGEM_PADRAO
    cep_cfg = _cep(str(romaneio_cfg.get("cep_origem", "") or ""))
    return cep_cfg or CEP_ORIGEM_PADRAO


def _resolver_cep_origem(config: dict[str, Any], cep_origem_informado: str) -> str:
    cep_informado = _cep(cep_origem_informado)
    if cep_informado:
        return cep_informado

    romaneio_cfg = config.get("romaneio", {}) if isinstance(config, dict) else {}
    if isinstance(romaneio_cfg, dict):
        cep_romaneio = _cep(str(romaneio_cfg.get("cep_origem", "") or ""))
        if cep_romaneio:
            _log_diag(f"Usando CEP origem do romaneio: {cep_romaneio}")
            return cep_romaneio

    transportadoras_cfg = config.get("transportadoras", {}) if isinstance(config, dict) else {}
    if isinstance(transportadoras_cfg, dict):
        for nome in ("braspress", "bauer", "trd"):
            sec = transportadoras_cfg.get(nome, {})
            if isinstance(sec, dict):
                cep_sec = _cep(str(sec.get("cep_origem", "") or ""))
                if cep_sec:
                    _log_diag(f"Usando CEP origem de transportadoras.{nome}: {cep_sec}")
                    return cep_sec

    _log_diag(f"Usando CEP origem padrão fixo: {CEP_ORIGEM_PADRAO}")
    return CEP_ORIGEM_PADRAO


def _dados_envio(extrator, pedidos: list[Any]) -> dict[str, Any]:
    if not pedidos:
        return {}

    grupos_caixa, caixas_complementares, total_boxes, total_volume, total_weight, total_valor = extrator._calcular_caixas_agrupadas(pedidos)

    destino_cep = extrator.obter_cep_local_entrega(pedidos[0].local_entrega or "")
    uf_destino = ""
    try:
        if hasattr(extrator, "obter_uf_local_entrega"):
            uf_destino = str(extrator.obter_uf_local_entrega(pedidos[0].local_entrega or "") or "").strip().upper()
    except Exception:
        uf_destino = ""
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


def _to_float_br(value: str) -> float:
    txt = re.sub(r"[^\d,.\-]", "", str(value or "").strip())
    if not txt:
        return 0.0
    if "," in txt and "." in txt:
        txt = txt.replace(".", "").replace(",", ".")
    elif "," in txt:
        txt = txt.replace(",", ".")
    return float(txt)


def _normalizar_romaneio_colado(texto: str) -> str:
    normalizado = str(texto or "").replace("\r\n", "\n").replace("\r", "\n")
    normalizado = re.sub(r"(?i)<br\s*/?>", "\n", normalizado)
    normalizado = re.sub(r"(?i)</p>", "\n", normalizado)
    normalizado = re.sub(r"(?i)<[^>]+>", " ", normalizado)
    normalizado = normalizado.replace("&nbsp;", " ")
    linhas = [re.sub(r"\s+", " ", linha).strip() for linha in normalizado.split("\n")]
    linhas = [linha for linha in linhas if linha]
    return "\n".join(linhas)


def _parse_dim_cm(raw: str) -> int:
    try:
        val = _to_float_br(raw)
    except ValueError:
        return 0
    # Aceita tanto dimensões em cm (31) quanto em metros (0,31).
    if 0 < val <= 3.5:
        return int(round(val * 100))
    return int(round(val))


def _extrair_uf_hint_texto(texto: str, pos_referencia: int = -1) -> str:
    """Tenta extrair uma UF (cidade/UF) próxima ao bloco do destinatário."""
    pattern = re.compile(
        r"\b([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ .'-]{2,})\s*/\s*([A-Za-z]{2})\b",
        re.IGNORECASE,
    )
    matches = list(pattern.finditer(texto or ""))
    if not matches:
        return ""

    if pos_referencia >= 0:
        depois = [m for m in matches if m.start() >= pos_referencia]
        if depois:
            return str(depois[0].group(2) or "").strip().upper()
    return str(matches[0].group(2) or "").strip().upper()


def _selecionar_cep_destino(texto: str, pos_referencia: int = -1, uf_hint: str = "") -> str:
    """
    Seleciona o CEP mais provável do destinatário.
    Regras:
    1) Preferir CEP após o CNPJ/CPF do destinatário.
    2) Se houver UF hint, priorizar CEPs compatíveis com essa UF.
    3) Priorizar CEP com rótulo explícito "CEP:".
    4) Em empate, usar o mais próximo da referência.
    """
    raw = str(texto or "")
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[int, str]] = set()

    label_pat = re.compile(r"\bCEP\s*:\s*(\d{2}\.?\d{3}-?\d{3}|\d{5}-?\d{3})\b", re.IGNORECASE)
    generic_pat = re.compile(r"\b(\d{5}-?\d{3})\b")

    for m in label_pat.finditer(raw):
        cep_digits = _cep(m.group(1))
        if len(cep_digits) != 8:
            continue
        key = (m.start(), cep_digits)
        if key in seen:
            continue
        seen.add(key)
        candidates.append(
            {
                "cep": cep_digits,
                "pos": m.start(),
                "labeled": True,
                "uf": _cep_para_uf(cep_digits) or "",
            }
        )

    for m in generic_pat.finditer(raw):
        cep_digits = _cep(m.group(1))
        if len(cep_digits) != 8:
            continue
        key = (m.start(), cep_digits)
        if key in seen:
            continue
        seen.add(key)
        candidates.append(
            {
                "cep": cep_digits,
                "pos": m.start(),
                "labeled": False,
                "uf": _cep_para_uf(cep_digits) or "",
            }
        )

    if not candidates:
        return ""

    ref = int(pos_referencia or 0)
    uf_ref = str(uf_hint or "").strip().upper()

    def _rank(c: dict[str, Any]) -> tuple[int, int, int, int]:
        pos = int(c.get("pos", 0) or 0)
        after = 0 if pos >= ref else 1
        dist = abs(pos - ref)
        labeled_penalty = 0 if bool(c.get("labeled")) else 1
        uf_penalty = 0
        if uf_ref:
            uf_penalty = 0 if str(c.get("uf", "")).upper() == uf_ref else 1
        return (uf_penalty, after, labeled_penalty, dist)

    candidates.sort(key=_rank)
    return str(candidates[0].get("cep", "") or "")


def _dados_envio_romaneio_colado(romaneio_colado: str) -> dict[str, Any]:
    texto = _normalizar_romaneio_colado(romaneio_colado)
    if not texto:
        raise ValueError("Romaneio colado vazio")

    m_cnpj = re.search(r"\bCNPJ/CPF\s*:\s*([0-9./-]{11,18})", texto, re.IGNORECASE)
    cnpj_destinatario = _digits(m_cnpj.group(1)) if m_cnpj else ""
    pos_ref = int(m_cnpj.end()) if m_cnpj else -1
    uf_hint = _extrair_uf_hint_texto(texto, pos_referencia=pos_ref)
    destino_cep = _selecionar_cep_destino(texto, pos_referencia=pos_ref, uf_hint=uf_hint)
    uf_destino = str(uf_hint or "").strip().upper()
    if not uf_destino and len(destino_cep) == 8:
        uf_destino = str(_cep_para_uf(destino_cep) or "").strip().upper()

    m_volumes = re.search(r"-\s*VOL(?:UME)?\s*:\s*(\d+)", texto, re.IGNORECASE)
    m_cubagem = re.search(r"-\s*CUBAGEM\s*:\s*([\d.,]+)\s*m3", texto, re.IGNORECASE)
    m_peso = re.search(r"-\s*PESO\s*:\s*([\d.,]+)\s*kg", texto, re.IGNORECASE)
    m_total = re.search(r"-\s*TOTAL\s*:\s*R\$\s*([\d.,]+)", texto, re.IGNORECASE)

    missing: list[str] = []
    if len(cnpj_destinatario) != 14:
        missing.append("CNPJ")
    if len(destino_cep) != 8:
        missing.append("CEP")
    if not m_volumes:
        missing.append("VOL")
    if not m_cubagem:
        missing.append("CUBAGEM")
    if not m_peso:
        missing.append("PESO")
    if not m_total:
        missing.append("TOTAL")
    if missing:
        raise ValueError(f"Romaneio colado inválido. Campos ausentes: {', '.join(missing)}")

    volumes = int(m_volumes.group(1))
    cubagem_m3 = _to_float_br(m_cubagem.group(1))
    peso = _to_float_br(m_peso.group(1))
    valor = _to_float_br(m_total.group(1))
    if volumes <= 0:
        raise ValueError("Romaneio colado inválido. Campo VOL deve ser maior que zero.")
    if peso <= 0:
        raise ValueError("Romaneio colado inválido. Campo PESO deve ser maior que zero.")
    if cubagem_m3 <= 0:
        raise ValueError("Romaneio colado inválido. Campo CUBAGEM deve ser maior que zero.")
    if valor < 0:
        raise ValueError("Romaneio colado inválido. Campo TOTAL não pode ser negativo.")

    cubagens: list[dict[str, Any]] = []

    # Ex.: "2 x Caixas fechadas - 1,650 kg - 0,044 m3 - 31x31x45"
    for m in re.finditer(
        r"(?im)^\s*(\d+)\s*x\s+.+?-\s*([\d.,]+)\s*kg\s*-\s*[\d.,]+\s*m3\s*-\s*([\d.,]+)\s*[xX×]\s*([\d.,]+)\s*[xX×]\s*([\d.,]+)\b",
        texto,
    ):
        try:
            qtd = int(m.group(1) or 0)
        except Exception:
            qtd = 0
        peso_por_volume_kg = _to_float_br(m.group(2))
        # Texto colado traz dimensões na ordem A×L×C (altura × largura × comprimento)
        a = _parse_dim_cm(m.group(3))
        l = _parse_dim_cm(m.group(4))
        c = _parse_dim_cm(m.group(5))
        if qtd <= 0 or a <= 0 or l <= 0 or c <= 0 or peso_por_volume_kg <= 0:
            continue
        cubagens.append(
            {
                "quantidade": qtd,
                "comprimento_cm": c,
                "largura_cm": l,
                "altura_cm": a,
                "peso_por_volume_kg": peso_por_volume_kg,
            }
        )

    comprimento_cm = 0
    largura_cm = 0
    altura_cm = 0
    for cub in cubagens:
        c = int(cub.get("comprimento_cm", 0) or 0)
        l = int(cub.get("largura_cm", 0) or 0)
        a = int(cub.get("altura_cm", 0) or 0)
        if c * l * a > comprimento_cm * largura_cm * altura_cm:
            comprimento_cm, largura_cm, altura_cm = c, l, a

    descricoes_itens = []
    for m_desc in re.finditer(r"(?im)^\s*(\S+.*?):\s*\d+\s*und\b", texto):
        descricoes_itens.append(m_desc.group(1).strip())

    return {
        "destino_cep": destino_cep,
        "uf_destino": uf_destino,
        "cnpj_destinatario": cnpj_destinatario,
        "peso": peso,
        "valor": valor,
        "volumes": volumes,
        "cubagem_m3": cubagem_m3,
        "comprimento_cm": comprimento_cm,
        "largura_cm": largura_cm,
        "altura_cm": altura_cm,
        "cubagens": cubagens,
        "descricoes_itens": descricoes_itens,
    }


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


def _kill_orphan_fretebot_chromes() -> None:
    """Mata processos Chrome órfãos de sessões anteriores do FreteBot.

    Procura por processos chrome.exe cujo command-line contenha
    o diretório .fretebot (user-data-dir dos providers Alfa e Rodonaves).
    Tenta wmic primeiro (rápido); se falhar usa Get-CimInstance (Windows 11+).
    """
    if sys.platform != "win32":
        return
    import subprocess as _sp
    fretebot_marker = os.path.join(os.path.expanduser("~"), ".fretebot").replace("/", "\\").lower()

    def _kill_pids_from_lines(lines: list[str]) -> None:
        pid = None
        cmd = ""
        for line in lines:
            line = line.strip()
            if not line:
                if pid is not None and fretebot_marker in cmd.lower():
                    try:
                        os.kill(pid, 9)
                        _log_diag(f"Matou Chrome órfão do FreteBot PID={pid}")
                    except OSError:
                        pass
                pid = None
                cmd = ""
                continue
            if line.startswith("CommandLine="):
                cmd = line[len("CommandLine="):]
            elif line.startswith("ProcessId="):
                try:
                    pid = int(line[len("ProcessId="):])
                except ValueError:
                    pid = None
        if pid is not None and fretebot_marker in cmd.lower():
            try:
                os.kill(pid, 9)
                _log_diag(f"Matou Chrome órfão do FreteBot PID={pid}")
            except OSError:
                pass

    # Tenta wmic (rápido, disponível em Windows 10 e versões antigas do 11)
    try:
        result = _sp.run(
            ["wmic", "process", "where", "Name='chrome.exe'", "get",
             "ProcessId,CommandLine", "/FORMAT:LIST"],
            capture_output=True, text=True, timeout=10,
            creationflags=_sp.CREATE_NO_WINDOW,
        )
        if result.returncode == 0:
            _kill_pids_from_lines(result.stdout.splitlines())
            return
    except Exception:
        pass

    # Fallback: PowerShell Get-CimInstance (Windows 11 22H2+)
    try:
        ps_cmd = (
            "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" "
            "| ForEach-Object { \"CommandLine=$($_.CommandLine)\"; \"ProcessId=$($_.ProcessId)\"; \"\" }"
        )
        result = _sp.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_cmd],
            capture_output=True, text=True, timeout=15,
            creationflags=_sp.CREATE_NO_WINDOW,
        )
        _kill_pids_from_lines(result.stdout.splitlines())
    except Exception as e:
        _log_diag(f"_kill_orphan_fretebot_chromes falhou: {e}")


# Prioridade de lentidão: maior = mais lento (baseado em testes reais).
# Usado para iniciar os mais lentos primeiro e para ordenar resultados.
_PRIORIDADE_LENTIDAO: dict[str, int] = {
    "TRD": 700,
    "ALFA": 600,
    "BRASPRESS": 500,
    "EUCATUR": 400,
    "COOPEX": 350,
    "RODONAVES": 300,
    "BAUER": 200,
    "AGEX": 100,
}


class TransportadoraSession:
    """Gerencia sessões persistentes dos providers (browsers já logados)."""

    IDLE_TIMEOUT_S: float = 600.0  # 10 minutos
    _IDLE_CHECK_INTERVAL_S: float = 60.0

    def __init__(self, config_path: Path | None = None):
        self.config = _carregar_config(config_path=config_path)
        self.providers: dict[str, Any] = {}
        self._inicializado = False
        self._ultimo_uso: dict[str, float] = {}
        self._idle_task: asyncio.Task | None = None

    async def inicializar(self, callback=None, login_status_callback=None, login_retry_callback=None):
        """Cria providers e faz pre-login em todos. callback(msg) para status.
        login_status_callback(nome, status) para status individual ('pending','ok','fail').
        login_retry_callback(nome) chamado quando login falha, para perguntar ao usuário."""
        # Importa providers sob demanda (lazy) para não atrasar a abertura da janela
        _ensure_provider_imports()
        # Mata processos Chrome órfãos de sessões anteriores do FreteBot
        _kill_orphan_fretebot_chromes()
        if self._inicializado:
            # Se já inicializado, apenas garante que providers fora do foco sejam encerrados.
            if MODO_FOCO_TRANSPORTADORA and self.providers:
                foco = str(MODO_FOCO_TRANSPORTADORA).strip().lower()
                for nome, prov in list(self.providers.items()):
                    if str(nome).strip().lower() == foco:
                        continue
                    try:
                        await prov.cleanup()
                        _log_diag(f"Modo foco {foco.upper()} ativo: cleanup {nome} OK")
                    except Exception as e:
                        _log_diag(f"Modo foco {foco.upper()} ativo: cleanup {nome} falhou: {e}")
                    self.providers.pop(nome, None)
            return
        transportadoras_cfg = self.config.get("transportadoras", {}) if isinstance(self.config, dict) else {}
        if MODO_FOCO_TRANSPORTADORA:
            if not isinstance(transportadoras_cfg, dict):
                transportadoras_cfg = {}
            transportadoras_cfg = dict(transportadoras_cfg)
            foco = str(MODO_FOCO_TRANSPORTADORA).strip().lower()
            for nome_cfg in ("braspress", "bauer", "trd", "agex", "eucatur", "rodonaves", "alfa", "coopex"):
                sec = transportadoras_cfg.get(nome_cfg)
                if not isinstance(sec, dict):
                    sec = {}
                sec = dict(sec)
                sec["habilitado"] = (nome_cfg == foco)
                transportadoras_cfg[nome_cfg] = sec
            _log_diag(f"Modo foco {foco.upper()} ativo: apenas essa transportadora fará pre-login.")

        def _cfg_secao(nome: str) -> dict[str, Any]:
            nested = transportadoras_cfg.get(nome, {}) if isinstance(transportadoras_cfg, dict) else {}
            if isinstance(nested, dict) and nested:
                return nested
            legacy = self.config.get(nome, {}) if isinstance(self.config, dict) else {}
            return legacy if isinstance(legacy, dict) else {}

        # Criar providers
        bcfg = _cfg_secao("braspress")
        if bcfg.get("habilitado", True):
            cnpj = str(bcfg.get("cnpj", "")).strip()
            senha = str(bcfg.get("senha", "")).strip()
            if cnpj and senha:
                headless_braspress = bool(bcfg.get("headless", True))
                self.providers["braspress"] = BraspressProvider(
                    cnpj=cnpj,
                    senha=senha,
                    headless=headless_braspress,
                )
                _log_diag(f"BRASPRESS sessão criada com headless={headless_braspress}")

        baucfg = _cfg_secao("bauer")
        if baucfg.get("habilitado", True):
            cotacao_url = str(baucfg.get("cotacao_url", "")).strip()
            cnpj_pagador = str(baucfg.get("cnpj_pagador", "")).strip()
            cnpj_remetente = str(baucfg.get("cnpj_remetente", "")).strip()
            cnpj_dest = str(baucfg.get("cnpj_destinatario", "")).strip()
            if cotacao_url and cnpj_pagador and cnpj_remetente and cnpj_dest:
                self.providers["bauer"] = BauerAutoProvider(
                    cotacao_url=cotacao_url,
                    cnpj_pagador=cnpj_pagador,
                    cnpj_remetente=cnpj_remetente,
                    cnpj_destinatario=cnpj_dest,
                    headless=bool(baucfg.get("headless", True)),
                )

        tcfg = _cfg_secao("trd")
        if tcfg.get("habilitado", True):
            email = str(tcfg.get("email", "")).strip()
            senha = str(tcfg.get("senha", "")).strip()
            if email and senha:
                foco_trd = str(MODO_FOCO_TRANSPORTADORA).strip().lower() == "trd"
                headless_trd = _trd_headless_config_value(tcfg, foco_trd)
                self.providers["trd"] = TRDProvider(email=email, senha=senha, headless=headless_trd)
                _log_diag(f"TRD sessão criada com headless={headless_trd}")

        if AGEXProvider is not None:
            acfg = _cfg_secao("agex")
            if acfg.get("habilitado", True):
                cnpj = str(acfg.get("cnpj", "")).strip()
                senha = str(acfg.get("senha", "")).strip()
                if cnpj and senha:
                    foco_agex = str(MODO_FOCO_TRANSPORTADORA).strip().lower() == "agex"
                    headless_agex = False if foco_agex else bool(acfg.get("headless", True))
                    self.providers["agex"] = AGEXProvider(
                        cnpj=cnpj, senha=senha,
                        cnpj_remetente=str(acfg.get("cnpj_remetente", "")).strip() or cnpj,
                        cnpj_destinatario=str(acfg.get("cnpj_destinatario", "")).strip(),
                        headless=headless_agex,
                    )
                    _log_diag(f"AGEX sessão criada com headless={headless_agex}")

        if EucaturProvider is not None:
            ecfg = _cfg_secao("eucatur")
            if ecfg.get("habilitado", True):
                dominio = str(ecfg.get("dominio", "")).strip()
                usuario = str(ecfg.get("usuario", "")).strip()
                senha_euc = str(ecfg.get("senha", "")).strip()
                if dominio and usuario and senha_euc:
                    foco_eucatur = str(MODO_FOCO_TRANSPORTADORA).strip().lower() == "eucatur"
                    headless_eucatur = False if foco_eucatur else bool(ecfg.get("headless", True))
                    self.providers["eucatur"] = EucaturProvider(
                        dominio=dominio,
                        usuario=usuario,
                        senha=senha_euc,
                        headless=headless_eucatur,
                    )
                    _log_diag(f"EUCATUR sessão criada com headless={headless_eucatur}")

        if RodonavesProvider is not None:
            rcfg = _cfg_secao("rodonaves")
            if rcfg.get("habilitado", True):
                if not _uf_atendida(rcfg.get("ufs_atendidas"), None):
                    _log_diag("RODONAVES ignorada por filtro de UF inválido")
                else:
                    dominio = str(rcfg.get("dominio", "RTE") or "RTE").strip()
                    usuario = str(rcfg.get("usuario", "")).strip()
                    senha = str(rcfg.get("senha", "")).strip()
                    cnpj_pagador = str(rcfg.get("cnpj_pagador", "")).strip()
                    if dominio and usuario and senha and cnpj_pagador:
                        foco_rodonaves = str(MODO_FOCO_TRANSPORTADORA).strip().lower() == "rodonaves"
                        headless_rodonaves = False if foco_rodonaves else bool(rcfg.get("headless", True))
                        self.providers["rodonaves"] = RodonavesProvider(
                            dominio=dominio,
                            usuario=usuario,
                            senha=senha,
                            cnpj_pagador=cnpj_pagador,
                            login_url=str(rcfg.get("login_url", "") or "").strip(),
                            cotacao_url=str(rcfg.get("cotacao_url", "") or "").strip(),
                            headless=headless_rodonaves,
                        )
                        _log_diag(f"RODONAVES sessão criada com headless={headless_rodonaves}")

        if AlfaProvider is not None:
            alcfg = _cfg_secao("alfa")
            if alcfg.get("habilitado", True):
                if not _uf_atendida(alcfg.get("ufs_atendidas"), None):
                    _log_diag("ALFA ignorada por filtro de UF inválido")
                else:
                    login = str(alcfg.get("login", "") or "").strip()
                    senha = str(alcfg.get("senha", "") or "").strip()
                    if login and senha:
                        headless_alfa = bool(alcfg.get("headless", False))
                        self.providers["alfa"] = AlfaProvider(
                            login=login,
                            senha=senha,
                            login_url=str(alcfg.get("login_url", "") or "").strip(),
                            cotacao_url=str(alcfg.get("cotacao_url", "") or "").strip(),
                            headless=headless_alfa,
                        )
                        _log_diag(f"ALFA sessão criada com headless={headless_alfa}")

        if CoopexProvider is not None:
            cocfg = _cfg_secao("coopex")
            if cocfg.get("habilitado", True):
                dominio = str(cocfg.get("dominio", "")).strip()
                usuario = str(cocfg.get("usuario", "")).strip()
                senha_co = str(cocfg.get("senha", "")).strip()
                if dominio and usuario and senha_co:
                    foco_coopex = str(MODO_FOCO_TRANSPORTADORA).strip().lower() == "coopex"
                    headless_coopex = False if foco_coopex else bool(cocfg.get("headless", True))
                    self.providers["coopex"] = CoopexProvider(
                        dominio=dominio,
                        usuario=usuario,
                        senha=senha_co,
                        headless=headless_coopex,
                    )
                    _log_diag(f"COOPEX sessão criada com headless={headless_coopex}")

        # Pre-login em todos (paralelo)
        _log_diag(f"Iniciando pre-login em {len(self.providers)} transportadoras...")
        if callback:
            callback(f"Fazendo login em {len(self.providers)} transportadoras...")

        async def _pre_login_one(nome, prov):
            # Alfa pode aguardar Turnstile manual; timeout maior
            timeout_s = 90 if nome.lower() == "alfa" else 45
            max_retries = 0 if nome.lower() == "alfa" else 1
            backoff = 3  # segundos entre tentativas

            if login_status_callback:
                login_status_callback(nome, "pending")

            for attempt in range(max_retries + 1):
                try:
                    _log_diag(f"Pre-login {nome}..." if attempt == 0 else f"Pre-login {nome} tentativa {attempt + 1}...")
                    if callback:
                        callback(f"Login: {nome}..." if attempt == 0 else f"Login: {nome} (tentativa {attempt + 1})...")
                    try:
                        await asyncio.wait_for(asyncio.shield(prov.pre_login()), timeout=timeout_s)
                    except asyncio.TimeoutError:
                        _log_diag(f"Pre-login {nome} timeout ({timeout_s}s) — login continuará na cotação")
                        if login_status_callback:
                            login_status_callback(nome, "fail")
                        if login_retry_callback:
                            login_retry_callback(nome)
                        return nome, False
                    _log_diag(f"Pre-login {nome} OK")
                    if login_status_callback:
                        login_status_callback(nome, "ok")
                    return nome, True
                except Exception as e:
                    is_connection_error = any(k in str(e) for k in ("ERR_CONNECTION", "ERR_NAME", "ERR_TIMED_OUT", "net::"))
                    if is_connection_error and attempt < max_retries:
                        wait = backoff * (attempt + 1)
                        _log_diag(f"Pre-login {nome} erro de rede ({e}), retry em {wait}s...")
                        await asyncio.sleep(wait)
                        continue
                    _log_diag(f"Pre-login {nome} falhou: {e}")
                    report_error_message(f"Pre-login {nome} falhou: {e}", context=f"prelogin_{nome}")
                    if login_status_callback:
                        login_status_callback(nome, "fail")
                    if login_retry_callback:
                        login_retry_callback(nome)
                    return nome, False

        results = await asyncio.gather(
            *[_pre_login_one(n, p) for n, p in self.providers.items()],
            return_exceptions=True,
        )

        ok_count = sum(1 for r in results if not isinstance(r, Exception) and r[1])
        _log_diag(f"Pre-login concluído: {ok_count}/{len(self.providers)} OK")

        # Oculta janelas "Default IME" que possam ter ficado visíveis
        try:
            from fretebot.providers._win_taskbar import ocultar_janelas_ime
            n = ocultar_janelas_ime()
            if n:
                _log_diag(f"Ocultou {n} janela(s) IME residual(is)")
        except Exception:
            pass

        if callback:
            callback(f"Login concluído: {ok_count}/{len(self.providers)} transportadoras prontas")
        self._inicializado = True
        agora = time.monotonic()
        for nome in self.providers:
            self._ultimo_uso[nome] = agora
        self._iniciar_verificador_ocioso()

    def registrar_uso(self, nome: str) -> None:
        """Atualiza timestamp de último uso de um provider."""
        self._ultimo_uso[nome] = time.monotonic()

    async def relogin_one(self, nome: str, login_status_callback=None) -> bool:
        """Refaz login de um provider específico. Retorna True se OK."""
        prov = self.providers.get(nome)
        if prov is None:
            _log_diag(f"relogin_one: provider '{nome}' não encontrado")
            return False
        _log_diag(f"Refazendo login de {nome}...")
        if login_status_callback:
            login_status_callback(nome, "pending")
        try:
            await prov.cleanup()
        except Exception:
            pass
        try:
            await prov.pre_login()
            _log_diag(f"Relogin {nome} OK")
            if login_status_callback:
                login_status_callback(nome, "ok")
            self._ultimo_uso[nome] = time.monotonic()
            return True
        except Exception as e:
            _log_diag(f"Relogin {nome} falhou: {e}")
            if login_status_callback:
                login_status_callback(nome, "fail")
            return False

    async def fechar_ociosos(self) -> None:
        """Fecha browsers de providers ociosos por mais de IDLE_TIMEOUT_S."""
        agora = time.monotonic()
        para_fechar: list[str] = []
        for nome in list(self.providers):
            ultimo = self._ultimo_uso.get(nome, agora)
            if agora - ultimo > self.IDLE_TIMEOUT_S:
                para_fechar.append(nome)
        for nome in para_fechar:
            prov = self.providers.pop(nome, None)
            tempo_ocioso = agora - self._ultimo_uso.pop(nome, agora)
            if prov is None:
                continue
            try:
                await prov.cleanup()
                _log_diag(f"Idle cleanup {nome} OK (ocioso por {tempo_ocioso:.0f}s)")
            except Exception as e:
                _log_diag(f"Idle cleanup {nome} falhou: {e}")

    def _iniciar_verificador_ocioso(self) -> None:
        """Inicia task em background que verifica providers ociosos."""
        if self._idle_task is not None and not self._idle_task.done():
            return

        async def _loop():
            while True:
                await asyncio.sleep(self._IDLE_CHECK_INTERVAL_S)
                try:
                    await self.fechar_ociosos()
                except Exception as e:
                    _log_diag(f"Erro no verificador de ociosidade: {e}")

        self._idle_task = asyncio.ensure_future(_loop())

    async def cleanup(self):
        """Fecha todos os browsers."""
        if self._idle_task is not None:
            self._idle_task.cancel()
            self._idle_task = None
        for nome, prov in self.providers.items():
            try:
                await prov.cleanup()
                _log_diag(f"Cleanup {nome} OK")
            except Exception as e:
                _log_diag(f"Cleanup {nome} falhou: {e}")
        self.providers.clear()
        self._ultimo_uso.clear()
        self._inicializado = False

    @property
    def pronto(self) -> bool:
        return self._inicializado


async def _executar_cotacoes_com_dados(
    *,
    config: dict[str, Any],
    dados: dict[str, Any],
    cep_origem: str,
    sessao: "TransportadoraSession | None" = None,
    progresso_callback: "Callable[[dict[str, Any]], None] | None" = None,
    cnpj_remetente: str = "",
    tipo_frete: str = "",
) -> list[ResultadoCotacao]:
    _ensure_provider_imports()

    def _emitir_progresso(
        *,
        concluidas: int,
        total: int,
        resultado: ResultadoCotacao | None = None,
    ) -> None:
        if progresso_callback is None:
            return
        try:
            progresso_callback(
                {
                    "concluidas": int(concluidas),
                    "total": int(total),
                    "resultado": resultado,
                }
            )
        except Exception as cb_error:
            _log_diag(f"Falha ao notificar progresso de cotação: {cb_error}")

    transportadoras_cfg = config.get("transportadoras", {}) if isinstance(config, dict) else {}
    if MODO_FOCO_TRANSPORTADORA:
        if not isinstance(transportadoras_cfg, dict):
            transportadoras_cfg = {}
        transportadoras_cfg = dict(transportadoras_cfg)
        foco = str(MODO_FOCO_TRANSPORTADORA).strip().lower()
        for nome_cfg in ("braspress", "bauer", "trd", "agex", "eucatur", "rodonaves", "coopex"):
            sec = transportadoras_cfg.get(nome_cfg)
            if not isinstance(sec, dict):
                sec = {}
            sec = dict(sec)
            sec["habilitado"] = (nome_cfg == foco)
            transportadoras_cfg[nome_cfg] = sec
        _log_diag(f"Modo foco {foco.upper()} ativo: apenas essa transportadora será cotada.")
        if sessao and getattr(sessao, "providers", None):
            for nome, prov in list(sessao.providers.items()):
                if str(nome).strip().lower() == foco:
                    continue
                try:
                    await prov.cleanup()
                    _log_diag(f"Modo foco {foco.upper()} ativo: cleanup {nome} OK")
                except Exception as e:
                    _log_diag(f"Modo foco {foco.upper()} ativo: cleanup {nome} falhou: {e}")
                sessao.providers.pop(nome, None)

    def _cfg_secao(nome: str) -> dict[str, Any]:
        nested = transportadoras_cfg.get(nome, {}) if isinstance(transportadoras_cfg, dict) else {}
        if isinstance(nested, dict) and nested:
            return nested
        legacy = config.get(nome, {}) if isinstance(config, dict) else {}
        return legacy if isinstance(legacy, dict) else {}

    origem = _resolver_cep_origem(config=config, cep_origem_informado=cep_origem)
    destino = _cep(str(dados.get("destino_cep", "") or ""))
    uf_destino_informada = str(dados.get("uf_destino", "") or "").strip().upper()
    if len(uf_destino_informada) != 2 or not uf_destino_informada.isalpha():
        uf_destino_informada = ""
    cnpj_destinatario = _digits(str(dados.get("cnpj_destinatario", "") or ""))
    try:
        peso = float(dados.get("peso", 0.0) or 0.0)
    except Exception:
        peso = 0.0
    try:
        valor = float(dados.get("valor", 0.0) or 0.0)
    except Exception:
        valor = 0.0

    if len(origem) != 8:
        _log_diag(f"CEP origem inválido: {origem}")
        return [ResultadoCotacao(transportadora="GERAL", status="erro", detalhes="CEP de origem inválido (use 8 dígitos)")]
    if len(destino) != 8:
        _log_diag(f"CEP destino inválido: {destino}")
        return [ResultadoCotacao(transportadora="GERAL", status="erro", detalhes="CEP de destino não encontrado nos pedidos")]
    if len(cnpj_destinatario) != 14:
        msg = "Cotação bloqueada: CNPJ do destinatário ausente ou inválido no romaneio."
        _log_diag(msg)
        return [ResultadoCotacao(transportadora="GERAL", status="erro", detalhes=msg)]
    if peso <= 0:
        msg = "Cotação bloqueada: peso total ausente ou inválido no romaneio."
        _log_diag(msg)
        return [ResultadoCotacao(transportadora="GERAL", status="erro", detalhes=msg)]

    cubagens_validas = _cubagens_validas(dados.get("cubagens"))
    if not cubagens_validas:
        msg = "Cotação bloqueada: romaneio sem cubagens válidas (tamanhos de caixa)."
        _log_diag(msg)
        return [ResultadoCotacao(transportadora="GERAL", status="erro", detalhes=msg)]

    try:
        volumes = int(dados.get("volumes", 0) or 0)
    except Exception:
        volumes = 0
    if volumes <= 0:
        volumes = sum(int(cub["quantidade"]) for cub in cubagens_validas)
    if volumes <= 0:
        msg = "Cotação bloqueada: quantidade de volumes inválida no romaneio."
        _log_diag(msg)
        return [ResultadoCotacao(transportadora="GERAL", status="erro", detalhes=msg)]
    volumes_cubagens = sum(int(cub["quantidade"]) for cub in cubagens_validas)
    if volumes_cubagens > 0 and volumes != volumes_cubagens:
        msg = (
            "Cotação bloqueada: volume total do romaneio diverge da soma das cubagens "
            f"(VOL={volumes} vs cubagens={volumes_cubagens})."
        )
        _log_diag(msg)
        return [ResultadoCotacao(transportadora="GERAL", status="erro", detalhes=msg)]

    try:
        cubagem_m3 = float(dados.get("cubagem_m3", 0.0) or 0.0)
    except Exception:
        cubagem_m3 = 0.0
    if cubagem_m3 <= 0:
        msg = "Cotação bloqueada: cubagem total ausente ou inválida no romaneio."
        _log_diag(msg)
        return [ResultadoCotacao(transportadora="GERAL", status="erro", detalhes=msg)]

    tasks: list[tuple[str, Any, dict[str, Any]]] = []  # (nome, provider, kwargs_coteir)
    erros_setup: list[ResultadoCotacao] = []
    uf_destino_cep = _cep_para_uf(destino)
    uf_destino = uf_destino_informada or uf_destino_cep
    if uf_destino_informada and uf_destino_cep and uf_destino_informada != uf_destino_cep:
        msg = (
            f"CEP de destino ({destino[:5]}-{destino[5:]}) pertence à UF {uf_destino_cep}, "
            f"mas o romaneio informa UF {uf_destino_informada}.\n\n"
            "Verifique se o CEP ou a cidade/UF do destinatário estão corretos no romaneio."
        )
        _log_diag(f"BLOQUEIO: divergência CEP/UF — {msg}")
        return [ResultadoCotacao(
            transportadora="GERAL",
            status="erro_divergencia_uf",
            detalhes=msg,
        )]
    _log_diag(
        f"Preparando cotações: origem={origem}, destino={destino}, peso={peso}, "
        f"valor={valor}, volumes={volumes}, cubagem={cubagem_m3:.4f}m³, "
        f"linhas_cubagem={len(cubagens_validas)}, UF={uf_destino or '?'}"
    )

    # BRASPRESS
    try:
        bcfg = _cfg_secao("braspress")
        if bcfg.get("habilitado", True):
            if not _uf_atendida(bcfg.get("ufs_atendidas"), uf_destino):
                _log_diag(f"BRASPRESS ignorada (UF {uf_destino} não atendida)")
            else:
                cnpj = str(bcfg.get("cnpj", "")).strip()
                senha = str(bcfg.get("senha", "")).strip()
                if cnpj and senha:
                    headless_braspress = bool(bcfg.get("headless", True))
                    provider = sessao.providers.get("braspress") if sessao else None
                    if provider is not None:
                        headless_atual = bool(getattr(provider, "headless", headless_braspress))
                        if headless_atual != headless_braspress:
                            _log_diag(
                                f"BRASPRESS: headless alterado ({headless_atual} -> {headless_braspress}), "
                                "reiniciando sessão do provider."
                            )
                            try:
                                await provider.cleanup()
                            except Exception as cleanup_error:
                                _log_diag(f"BRASPRESS cleanup ao trocar headless falhou: {cleanup_error}")
                            provider = None
                    if provider is None:
                        provider = BraspressProvider(
                            cnpj=cnpj,
                            senha=senha,
                            headless=headless_braspress,
                        )
                        if sessao is not None:
                            sessao.providers["braspress"] = provider
                    primeira_cub = cubagens_validas[0]
                    _log_diag(
                        f"BRASPRESS preparada (cnpj={cnpj[:6]}..., linhas_cubagem={len(cubagens_validas)}, "
                        f"headless={headless_braspress})"
                    )
                    if sessao is not None:
                        sessao.registrar_uso("braspress")
                    _bp_kwargs = dict(
                        origem=origem,
                        destino=destino,
                        peso=peso,
                        valor=valor,
                        cnpj_destinatario=cnpj_destinatario,
                        volumes=volumes,
                        comprimento_cm=int(primeira_cub["comprimento_cm"]),
                        largura_cm=int(primeira_cub["largura_cm"]),
                        altura_cm=int(primeira_cub["altura_cm"]),
                        cubagens=cubagens_validas,
                    )
                    if cnpj_remetente:
                        _bp_kwargs["cnpj_remetente"] = cnpj_remetente
                        _bp_kwargs["tipo_frete"] = tipo_frete or "2"
                    tasks.append(("BRASPRESS", provider, _bp_kwargs))
                else:
                    _log_diag("BRASPRESS não configurada (CNPJ/senha ausentes)")
    except Exception as e:
        _log_diag(f"Erro ao preparar BRASPRESS: {e}")
        erros_setup.append(ResultadoCotacao(transportadora="BRASPRESS", status="erro", detalhes=str(e)))

    # BAUER
    try:
        baucfg = _cfg_secao("bauer")
        if baucfg.get("habilitado", True):
            if not _uf_atendida(baucfg.get("ufs_atendidas"), uf_destino):
                _log_diag(f"BAUER ignorada (UF {uf_destino} não atendida)")
            else:
                cotacao_url = str(baucfg.get("cotacao_url", "")).strip()
                bau_cnpj_pag = str(baucfg.get("cnpj_pagador", "")).strip()
                bau_cnpj_rem = str(baucfg.get("cnpj_remetente", "")).strip()
                cnpj_dest = cnpj_destinatario
                if cotacao_url and bau_cnpj_pag and bau_cnpj_rem and cnpj_dest:
                    cubagens_bauer = []
                    for cub in cubagens_validas:
                        qtd = int(cub["quantidade"])
                        if qtd <= 0:
                            continue
                        cubagens_bauer.append(
                            {
                                "quantidade": qtd,
                                "altura_m": int(cub["altura_cm"]) / 100.0,
                                "largura_m": int(cub["largura_cm"]) / 100.0,
                                "profundidade_m": int(cub["comprimento_cm"]) / 100.0,
                            }
                        )
                    if not cubagens_bauer:
                        msg = "BAUER bloqueada: romaneio sem cubagens válidas."
                        _log_diag(msg)
                        erros_setup.append(
                            ResultadoCotacao(transportadora="BAUER", status="erro", detalhes=msg)
                        )
                    else:
                        vol = sum(int(c["quantidade"]) for c in cubagens_bauer)
                        primeira = cubagens_bauer[0]
                        alt_m = float(primeira["altura_m"])
                        larg_m = float(primeira["largura_m"])
                        prof_m = float(primeira["profundidade_m"])
                        provider = sessao.providers.get("bauer") if sessao else None
                        if provider is None:
                            provider = BauerAutoProvider(
                                cotacao_url=cotacao_url,
                                cnpj_pagador=bau_cnpj_pag,
                                cnpj_remetente=bau_cnpj_rem,
                                cnpj_destinatario=cnpj_dest,
                                headless=bool(baucfg.get("headless", True)),
                                quantidade=vol,
                                altura_m=alt_m,
                                largura_m=larg_m,
                                profundidade_m=prof_m,
                                cubagens=cubagens_bauer,
                            )
                        else:
                            provider.quantidade = vol
                            provider.altura_m = alt_m
                            provider.largura_m = larg_m
                            provider.profundidade_m = prof_m
                            if hasattr(provider, "cubagens"):
                                provider.cubagens = cubagens_bauer
                            if hasattr(provider, "cnpj_destinatario"):
                                provider.cnpj_destinatario = re.sub(r"\D", "", cnpj_dest or "")
                        _log_diag(
                            f"BAUER preparada: linhas_cubagem={len(cubagens_bauer)}, volumes={vol}"
                        )
                        if sessao is not None:
                            sessao.registrar_uso("bauer")
                        _bauer_kwargs = dict(
                            origem=origem,
                            destino=destino,
                            peso=peso,
                            valor=valor,
                            cubagens=cubagens_bauer,
                        )
                        if cnpj_remetente:
                            provider.cnpj_remetente = re.sub(r"\D", "", cnpj_remetente)
                            provider.cnpj_destinatario = re.sub(r"\D", "", bau_cnpj_pag)
                            _bauer_kwargs["destino"] = _resolver_cep_origem(config, "")
                            _bauer_kwargs["tipo_frete"] = "fob"
                        tasks.append(("BAUER", provider, _bauer_kwargs))
                else:
                    _log_diag("BAUER não configurada (parâmetros ausentes)")
    except Exception as e:
        _log_diag(f"Erro ao preparar BAUER: {e}")
        erros_setup.append(ResultadoCotacao(transportadora="BAUER", status="erro", detalhes=str(e)))

    # TRD
    try:
        tcfg = _cfg_secao("trd")
        if tcfg.get("habilitado", True):
            if not _uf_atendida(tcfg.get("ufs_atendidas"), uf_destino):
                _log_diag(f"TRD ignorada (UF {uf_destino} não atendida)")
            else:
                email = str(tcfg.get("email", "")).strip()
                senha = str(tcfg.get("senha", "")).strip()
                if email and senha:
                    foco_trd = str(MODO_FOCO_TRANSPORTADORA).strip().lower() == "trd"
                    headless_trd = _trd_headless_config_value(tcfg, foco_trd)
                    provider = sessao.providers.get("trd") if sessao else None
                    if provider is not None:
                        headless_atual = bool(getattr(provider, "headless", headless_trd))
                        if headless_atual != headless_trd:
                            _log_diag(
                                f"TRD: headless alterado ({headless_atual} -> {headless_trd}), "
                                "reiniciando sessão do provider."
                            )
                            try:
                                await provider.cleanup()
                            except Exception as cleanup_error:
                                _log_diag(f"TRD cleanup ao trocar headless falhou: {cleanup_error}")
                            provider = None
                    if provider is None:
                        provider = TRDProvider(email=email, senha=senha, headless=headless_trd)
                        if sessao is not None:
                            sessao.providers["trd"] = provider
                    _log_diag(f"TRD preparada (headless={headless_trd})")
                    if sessao is not None:
                        sessao.registrar_uso("trd")
                    _trd_kwargs = dict(
                        origem=origem,
                        destino=destino,
                        peso=peso,
                        valor=valor,
                        volumes=volumes,
                        cubagens=cubagens_validas,
                        cnpj_destinatario=cnpj_destinatario,
                    )
                    if cnpj_remetente:
                        _trd_kwargs["cnpj_remetente"] = cnpj_remetente
                        _trd_kwargs["cep_remetente"] = origem
                    tasks.append(("TRD", provider, _trd_kwargs))
                else:
                    _log_diag("TRD não configurada (email/senha ausentes)")
    except Exception as e:
        _log_diag(f"Erro ao preparar TRD: {e}")
        erros_setup.append(ResultadoCotacao(transportadora="TRD", status="erro", detalhes=str(e)))

    # AGEX — ignorada no modo fornecedor
    if cnpj_remetente:
        _log_diag("AGEX ignorada no modo fornecedor")
    else:
      try:
        if AGEXProvider is not None:
            acfg = _cfg_secao("agex")
            if acfg.get("habilitado", True):
                if (uf_destino or "").upper() in {"RS", "SC"}:
                    _log_diag(f"AGEX bloqueada para UF {uf_destino} (não atende este estado)")
                elif not _uf_atendida(acfg.get("ufs_atendidas"), uf_destino):
                    _log_diag(f"AGEX ignorada (UF {uf_destino} não atendida)")
                else:
                    cnpj = str(acfg.get("cnpj", "")).strip()
                    senha = str(acfg.get("senha", "")).strip()
                    if cnpj and senha:
                        foco_agex = str(MODO_FOCO_TRANSPORTADORA).strip().lower() == "agex"
                        headless_agex = False if foco_agex else bool(acfg.get("headless", True))
                        cnpj_rem = _digits(str(acfg.get("cnpj_remetente", "")).strip() or cnpj)
                        cnpj_dest = cnpj_destinatario
                        descricao_mercadoria = str(acfg.get("descricao_mercadoria", "Mercadoria"))
                        tipo_produto = str(acfg.get("tipo_produto", "Artigos Esportivos"))
                        cubagens_agex = []
                        for cub in cubagens_validas:
                            qtd = int(cub["quantidade"])
                            c_cm = int(cub["comprimento_cm"])
                            l_cm = int(cub["largura_cm"])
                            a_cm = int(cub["altura_cm"])
                            cubagens_agex.append(
                                {
                                    "quantidade": qtd,
                                    "comprimento_m": c_cm / 100.0,
                                    "largura_m": l_cm / 100.0,
                                    "altura_m": a_cm / 100.0,
                                }
                            )
                        if not cubagens_agex:
                            msg = "AGEX bloqueada: romaneio sem tamanhos de caixa (cubagens) válidos."
                            _log_diag(msg)
                            erros_setup.append(ResultadoCotacao(transportadora="AGEX", status="erro", detalhes=msg))
                        else:
                            vol = sum(int(c["quantidade"]) for c in cubagens_agex)
                            primeira = cubagens_agex[0]
                            alt_m = float(primeira["altura_m"])
                            larg_m = float(primeira["largura_m"])
                            comp_m = float(primeira["comprimento_m"])
                            provider = sessao.providers.get("agex") if sessao else None
                            if provider is not None:
                                headless_atual = bool(getattr(provider, "headless", headless_agex))
                                if headless_atual != headless_agex:
                                    _log_diag(
                                        f"AGEX: headless alterado ({headless_atual} -> {headless_agex}), "
                                        "reiniciando sessão do provider."
                                    )
                                    try:
                                        await provider.cleanup()
                                    except Exception as cleanup_error:
                                        _log_diag(f"AGEX cleanup ao trocar headless falhou: {cleanup_error}")
                                    provider = None
                            if provider is None:
                                provider = AGEXProvider(
                                    cnpj=cnpj,
                                    senha=senha,
                                    cnpj_remetente=cnpj_rem,
                                    cnpj_destinatario=cnpj_dest,
                                    cep_origem=origem,
                                    cep_destino=destino,
                                    descricao_mercadoria=descricao_mercadoria,
                                    tipo_produto=tipo_produto,
                                    volumes=vol,
                                    altura_m=alt_m,
                                    largura_m=larg_m,
                                    comprimento_m=comp_m,
                                    cubagens=cubagens_agex,
                                    headless=headless_agex,
                                )
                                if sessao is not None:
                                    sessao.providers["agex"] = provider
                            # Sessão pré-logada: atualizar sempre os dados da carga corrente.
                            if hasattr(provider, "atualizar_carga"):
                                provider.atualizar_carga(
                                    volumes=vol,
                                    altura_m=alt_m,
                                    largura_m=larg_m,
                                    comprimento_m=comp_m,
                                    cnpj_remetente=cnpj_rem,
                                    cnpj_destinatario=cnpj_dest,
                                    cep_origem=origem,
                                    cep_destino=destino,
                                    descricao_mercadoria=descricao_mercadoria,
                                    tipo_produto=tipo_produto,
                                    cubagens=cubagens_agex,
                                )
                            _log_diag(
                                f"AGEX preparada: peso={peso:.3f}kg, vol={vol}, "
                                f"dims={comp_m:.2f}x{larg_m:.2f}x{alt_m:.2f}m, "
                                f"linhas_cubagem={len(cubagens_agex)}, headless={headless_agex}"
                            )
                            if sessao is not None:
                                sessao.registrar_uso("agex")
                            _agex_kwargs = dict(
                                origem=cnpj_rem,
                                destino=cnpj_dest,
                                peso=peso,
                                valor=valor,
                            )
                            tasks.append(("AGEX", provider, _agex_kwargs))
                    else:
                        _log_diag("AGEX não configurada (CNPJ/senha ausentes)")
      except Exception as e:
        _log_diag(f"Erro ao preparar AGEX: {e}")
        erros_setup.append(ResultadoCotacao(transportadora="AGEX", status="erro", detalhes=str(e)))

    # Eucatur (SSW)
    try:
        if EucaturProvider is not None:
            ecfg = _cfg_secao("eucatur")
            if ecfg.get("habilitado", True):
                if not _uf_atendida(ecfg.get("ufs_atendidas"), uf_destino):
                    _log_diag(f"Eucatur ignorada (UF {uf_destino} não atendida)")
                else:
                    dominio = str(ecfg.get("dominio", "")).strip()
                    usuario = str(ecfg.get("usuario", "")).strip()
                    senha_euc = str(ecfg.get("senha", "")).strip()
                    if dominio and usuario and senha_euc:
                        foco_eucatur = str(MODO_FOCO_TRANSPORTADORA).strip().lower() == "eucatur"
                        headless_eucatur = False if foco_eucatur else bool(ecfg.get("headless", True))
                        provider = sessao.providers.get("eucatur") if sessao else None
                        if provider is not None:
                            headless_atual = bool(getattr(provider, "headless", headless_eucatur))
                            if headless_atual != headless_eucatur:
                                _log_diag(
                                    f"EUCATUR: headless alterado ({headless_atual} -> {headless_eucatur}), "
                                    "reiniciando sessão do provider."
                                )
                                try:
                                    await provider.cleanup()
                                except Exception as cleanup_error:
                                    _log_diag(f"EUCATUR cleanup ao trocar headless falhou: {cleanup_error}")
                                provider = None
                        if provider is None:
                            provider = EucaturProvider(
                                dominio=dominio,
                                usuario=usuario,
                                senha=senha_euc,
                                headless=headless_eucatur,
                            )
                            if sessao is not None:
                                sessao.providers["eucatur"] = provider
                        _log_diag(f"EUCATUR preparada (headless={headless_eucatur})")
                        if sessao is not None:
                            sessao.registrar_uso("eucatur")
                        _euc_kwargs = dict(
                            origem=origem,
                            destino=destino,
                            peso=peso,
                            valor=valor,
                            volumes=volumes,
                            cubagem_m3=cubagem_m3,
                            cubagens=cubagens_validas,
                            cnpj_remetente="40223106000179",
                            cnpj_destinatario=cnpj_destinatario,
                        )
                        if cnpj_remetente:
                            _euc_kwargs["cnpj_pagador"] = "40223106000179"
                            _euc_kwargs["cnpj_remetente"] = cnpj_remetente
                            _euc_kwargs["cnpj_destinatario"] = "40223106000179"
                            _euc_kwargs["destino"] = _resolver_cep_origem(config, "")
                            _euc_kwargs["tipo_frete"] = "2"
                        tasks.append(("EUCATUR", provider, _euc_kwargs))
                    else:
                        _log_diag("Eucatur não configurada (domínio/usuário/senha ausentes)")
    except Exception as e:
        _log_diag(f"Erro ao preparar Eucatur: {e}")
        erros_setup.append(ResultadoCotacao(transportadora="EUCATUR", status="erro", detalhes=str(e)))

    # Rodonaves (SSW) — ignorada no modo fornecedor
    if cnpj_remetente:
        _log_diag("RODONAVES ignorada no modo fornecedor")
    else:
      try:
        if RodonavesProvider is not None:
            rcfg = _cfg_secao("rodonaves")
            if rcfg.get("habilitado", True):
                if not _uf_atendida(rcfg.get("ufs_atendidas"), uf_destino):
                    _log_diag(f"Rodonaves ignorada (UF {uf_destino} não atendida)")
                else:
                    dominio = str(rcfg.get("dominio", "RTE") or "RTE").strip()
                    usuario = str(rcfg.get("usuario", "")).strip()
                    senha = str(rcfg.get("senha", "")).strip()
                    cnpj_pagador = _digits(str(rcfg.get("cnpj_pagador", "") or ""))
                    if dominio and usuario and senha and len(cnpj_pagador) == 14:
                        foco_rodonaves = str(MODO_FOCO_TRANSPORTADORA).strip().lower() == "rodonaves"
                        headless_rodonaves = False if foco_rodonaves else bool(rcfg.get("headless", True))
                        provider = sessao.providers.get("rodonaves") if sessao else None
                        if provider is not None:
                            headless_atual = bool(getattr(provider, "headless", headless_rodonaves))
                            if headless_atual != headless_rodonaves:
                                _log_diag(
                                    f"RODONAVES: headless alterado ({headless_atual} -> {headless_rodonaves}), "
                                    "reiniciando sessão do provider."
                                )
                                try:
                                    await provider.cleanup()
                                except Exception as cleanup_error:
                                    _log_diag(f"RODONAVES cleanup ao trocar headless falhou: {cleanup_error}")
                                provider = None
                        if provider is None:
                            provider = RodonavesProvider(
                                dominio=dominio,
                                usuario=usuario,
                                senha=senha,
                                cnpj_pagador=cnpj_pagador,
                                login_url=str(rcfg.get("login_url", "") or "").strip(),
                                cotacao_url=str(rcfg.get("cotacao_url", "") or "").strip(),
                                headless=headless_rodonaves,
                            )
                            if sessao is not None:
                                sessao.providers["rodonaves"] = provider
                        _log_diag(f"RODONAVES preparada (headless={headless_rodonaves})")
                        if sessao is not None:
                            sessao.registrar_uso("rodonaves")
                        _rodo_kwargs = dict(
                            origem=origem,
                            destino=destino,
                            peso=peso,
                            valor=valor,
                            volumes=volumes,
                            cubagem_m3=cubagem_m3,
                            cubagens=cubagens_validas,
                            cnpj_remetente=cnpj_pagador,
                            cnpj_destinatario=cnpj_destinatario,
                            preencher_cep_origem=bool(_cep(cep_origem)),
                        )
                        tasks.append(("RODONAVES", provider, _rodo_kwargs))
                    else:
                        _log_diag("RODONAVES não configurada (domínio/usuário/senha/cnpj_pagador ausentes)")
      except Exception as e:
        _log_diag(f"Erro ao preparar RODONAVES: {e}")
        erros_setup.append(ResultadoCotacao(transportadora="RODONAVES", status="erro", detalhes=str(e)))

    # Alfa
    try:
        if AlfaProvider is not None:
            alcfg = _cfg_secao("alfa")
            if alcfg.get("habilitado", True):
                descricoes_itens = dados.get("descricoes_itens", [])
                if any("PICOLO" in d.upper() for d in descricoes_itens):
                    _log_diag("ALFA ignorada (item PICOLO encontrado no romaneio)")
                elif not _uf_atendida(alcfg.get("ufs_atendidas"), uf_destino):
                    _log_diag(f"ALFA ignorada (UF {uf_destino} não atendida)")
                else:
                    login = str(alcfg.get("login", "") or "").strip()
                    senha = str(alcfg.get("senha", "") or "").strip()
                    cnpj_rem = str(alcfg.get("cnpj_remetente", "") or "").strip()
                    if login and senha and cnpj_rem:
                        headless_alfa = bool(alcfg.get("headless", False))
                        provider = sessao.providers.get("alfa") if sessao else None
                        if provider is not None:
                            headless_atual = bool(getattr(provider, "headless", headless_alfa))
                            if headless_atual != headless_alfa:
                                _log_diag(
                                    f"ALFA: headless alterado ({headless_atual} -> {headless_alfa}), "
                                    "reiniciando sessão do provider."
                                )
                                try:
                                    await provider.cleanup()
                                except Exception as cleanup_error:
                                    _log_diag(f"ALFA cleanup ao trocar headless falhou: {cleanup_error}")
                                provider = None
                        if provider is None:
                            provider = AlfaProvider(
                                login=login,
                                senha=senha,
                                login_url=str(alcfg.get("login_url", "") or "").strip(),
                                cotacao_url=str(alcfg.get("cotacao_url", "") or "").strip(),
                                headless=headless_alfa,
                            )
                            if sessao is not None:
                                sessao.providers["alfa"] = provider
                        _log_diag(f"ALFA preparada (headless={headless_alfa})")
                        if sessao is not None:
                            sessao.registrar_uso("alfa")
                        _alfa_kwargs = dict(
                            origem=origem,
                            destino=destino,
                            peso=peso,
                            valor=valor,
                            volumes=volumes,
                            cubagem_m3=cubagem_m3,
                            cubagens=cubagens_validas,
                            cnpj_remetente=cnpj_rem,
                            cnpj_destinatario=cnpj_destinatario,
                        )
                        if cnpj_remetente:
                            _alfa_kwargs["cnpj_remetente"] = cnpj_remetente
                            _alfa_kwargs["cnpj_destinatario"] = cnpj_rem
                            _alfa_kwargs["destino"] = _resolver_cep_origem(config, "")
                            _alfa_kwargs["tipo_pagador"] = "2"
                        tasks.append(("ALFA", provider, _alfa_kwargs))
                    else:
                        _log_diag("ALFA não configurada (login/senha/cnpj_remetente ausentes)")
    except Exception as e:
        _log_diag(f"Erro ao preparar ALFA: {e}")
        erros_setup.append(ResultadoCotacao(transportadora="ALFA", status="erro", detalhes=str(e)))

    # COOPEX (SSW)
    try:
        if CoopexProvider is not None:
            cocfg = _cfg_secao("coopex")
            if cocfg.get("habilitado", True):
                if not _uf_atendida(cocfg.get("ufs_atendidas"), uf_destino):
                    _log_diag(f"COOPEX ignorada (UF {uf_destino} não atendida)")
                else:
                    dominio = str(cocfg.get("dominio", "")).strip()
                    usuario = str(cocfg.get("usuario", "")).strip()
                    senha_co = str(cocfg.get("senha", "")).strip()
                    if dominio and usuario and senha_co:
                        foco_coopex = str(MODO_FOCO_TRANSPORTADORA).strip().lower() == "coopex"
                        headless_coopex = False if foco_coopex else bool(cocfg.get("headless", True))
                        provider = sessao.providers.get("coopex") if sessao else None
                        if provider is not None:
                            headless_atual = bool(getattr(provider, "headless", headless_coopex))
                            if headless_atual != headless_coopex:
                                _log_diag(
                                    f"COOPEX: headless alterado ({headless_atual} -> {headless_coopex}), "
                                    "reiniciando sessão do provider."
                                )
                                try:
                                    await provider.cleanup()
                                except Exception as cleanup_error:
                                    _log_diag(f"COOPEX cleanup ao trocar headless falhou: {cleanup_error}")
                                provider = None
                        if provider is None:
                            provider = CoopexProvider(
                                dominio=dominio,
                                usuario=usuario,
                                senha=senha_co,
                                headless=headless_coopex,
                            )
                            if sessao is not None:
                                sessao.providers["coopex"] = provider
                        _log_diag(f"COOPEX preparada (headless={headless_coopex})")
                        if sessao is not None:
                            sessao.registrar_uso("coopex")
                        _co_kwargs = dict(
                            origem=origem,
                            destino=destino,
                            peso=peso,
                            valor=valor,
                            volumes=volumes,
                            cubagem_m3=cubagem_m3,
                            cubagens=cubagens_validas,
                            cnpj_remetente="40223106000179",
                            cnpj_destinatario=cnpj_destinatario,
                        )
                        if cnpj_remetente:
                            _co_kwargs["cnpj_pagador"] = "40223106000179"
                            _co_kwargs["cnpj_remetente"] = cnpj_remetente
                            _co_kwargs["cnpj_destinatario"] = "40223106000179"
                            _co_kwargs["destino"] = _resolver_cep_origem(config, "")
                            _co_kwargs["tipo_frete"] = "2"
                        tasks.append(("COOPEX", provider, _co_kwargs))
                    else:
                        _log_diag("COOPEX não configurada (domínio/usuário/senha ausentes)")
    except Exception as e:
        _log_diag(f"Erro ao preparar COOPEX: {e}")
        erros_setup.append(ResultadoCotacao(transportadora="COOPEX", status="erro", detalhes=str(e)))

    # Executa primeiro as transportadoras mais lentas para reduzir tempo total.
    # Maior número = tendência de maior duração (baseado em testes reais).
    tasks.sort(key=lambda t: _PRIORIDADE_LENTIDAO.get(str(t[0]).upper(), 0), reverse=True)

    # Cotações em paralelo (configurável, padrão 3)
    fb_cfg = config.get("fretebot", {}) if isinstance(config, dict) else {}
    max_paralelo = max(1, min(7, int(fb_cfg.get("max_paralelo", 3) or 3)))
    nomes_tasks = ", ".join(nome for nome, _provider, _kwargs in tasks)
    _log_diag(f"Executando {len(tasks)} cotações em paralelo (máx {max_paralelo}): {nomes_tasks}")
    resultados: list[ResultadoCotacao] = []
    total_cotacoes = len(tasks) + len(erros_setup)
    concluidas = 0
    if total_cotacoes > 0:
        _emitir_progresso(concluidas=concluidas, total=total_cotacoes)
    for erro_setup in erros_setup:
        resultados.append(erro_setup)
        concluidas += 1
        _emitir_progresso(concluidas=concluidas, total=total_cotacoes, resultado=erro_setup)
    semaforo = asyncio.Semaphore(max_paralelo)

    timeout_por_transportadora_s = 120

    async def _exec(i: int, nome: str, provider: Any, kwargs: dict[str, Any]):
        is_alfa = nome.upper() == "ALFA"
        if not is_alfa:
            await semaforo.acquire()
            _log_diag(f"Semáforo adquirido: {nome} (posição {i})")
        try:
            effective_timeout = timeout_por_transportadora_s + 60 if is_alfa else timeout_por_transportadora_s
            coro = provider.coteir(**kwargs)
            cotacao = await asyncio.wait_for(coro, timeout=effective_timeout)
            return i, nome, provider, kwargs, cotacao, None
        except asyncio.TimeoutError:
            return i, nome, provider, kwargs, None, TimeoutError(
                f"Timeout de {effective_timeout}s na cotação {nome}"
            )
        except asyncio.CancelledError as exc:
            detalhe = str(exc).strip() or "sem detalhe"
            return i, nome, provider, kwargs, None, RuntimeError(
                f"Cotação {nome} cancelada: {detalhe}"
            )
        except Exception as exc:
            return i, nome, provider, kwargs, None, exc
        finally:
            if not is_alfa:
                semaforo.release()

    def _processar_resultado(res, resultados, falhas_para_retry):
        """Processa resultado de _exec, retorna (ResultadoCotacao|None, ok: bool)."""
        nonlocal concluidas

        if not isinstance(res, tuple) or len(res) != 6:
            msg = f"Executor retornou formato inesperado de resultado: {type(res).__name__}"
            _log_diag(msg)
            r = ResultadoCotacao(transportadora="GERAL", status="erro", detalhes=msg)
            concluidas += 1
            resultados.append(r)
            _emitir_progresso(concluidas=concluidas, total=total_cotacoes, resultado=r)
            return

        _i, nome_task, provider_task, kwargs_task, cotacao, erro = res

        if isinstance(erro, BaseException):
            import traceback
            tb = ''.join(traceback.format_exception(type(erro), erro, erro.__traceback__))
            _log_diag(f"Erro em cotação {nome_task}: {type(erro).__name__}: {erro}\n{tb}")
            report_error(type(erro), erro, erro.__traceback__, context=f"cotacao_{nome_task}")
            if falhas_para_retry is not None:
                falhas_para_retry.append((nome_task, provider_task, kwargs_task))
                _log_diag(f"{nome_task} enfileirada para retry após as demais completarem")
            else:
                r = ResultadoCotacao(
                    transportadora=nome_task, status="erro",
                    detalhes=f"{type(erro).__name__}: {erro}",
                )
                concluidas += 1
                resultados.append(r)
                _emitir_progresso(concluidas=concluidas, total=total_cotacoes, resultado=r)
            return

        if erro is not None:
            _log_diag(f"Erro em cotação {nome_task}: {erro}")
            if falhas_para_retry is not None:
                falhas_para_retry.append((nome_task, provider_task, kwargs_task))
                _log_diag(f"{nome_task} enfileirada para retry após as demais completarem")
            else:
                r = ResultadoCotacao(
                    transportadora=nome_task, status="erro", detalhes=str(erro),
                )
                concluidas += 1
                resultados.append(r)
                _emitir_progresso(concluidas=concluidas, total=total_cotacoes, resultado=r)
            return

        if cotacao is not None:
            try:
                transportadora = str(getattr(cotacao, "transportadora", nome_task))
                valor_frete = float(getattr(cotacao, "valor_frete", 0.0))
                prazo_dias = int(getattr(cotacao, "prazo_dias", 0))
                detalhes = getattr(cotacao, "restricoes", None)
            except Exception as parse_exc:
                _log_diag(f"Resultado inválido em {nome_task}: {parse_exc}")
                r = ResultadoCotacao(
                    transportadora=nome_task, status="erro",
                    detalhes=f"Resultado inválido: {parse_exc}",
                )
                concluidas += 1
                resultados.append(r)
                _emitir_progresso(concluidas=concluidas, total=total_cotacoes, resultado=r)
                return

            r = ResultadoCotacao(
                transportadora=transportadora, status="ok",
                valor_frete=valor_frete, prazo_dias=prazo_dias, detalhes=detalhes,
            )
            resultados.append(r)
            concluidas += 1
            _log_diag(f"✅ {transportadora}: R$ {valor_frete:.2f} - {prazo_dias} dias")
            _emitir_progresso(concluidas=concluidas, total=total_cotacoes, resultado=r)
        else:
            detalhe = None
            if provider_task is not None:
                detalhe = getattr(provider_task, "last_error", None)
            if detalhe:
                _log_diag(f"{nome_task} retornou None: {detalhe}")
            else:
                _log_diag(f"{nome_task} retornou None (sem resultado)")
                detalhe = "Sem resultado"
            report_error_message(f"{nome_task} retornou None: {detalhe}", context=f"cotacao_{nome_task}")
            if falhas_para_retry is not None:
                falhas_para_retry.append((nome_task, provider_task, kwargs_task))
                _log_diag(f"{nome_task} enfileirada para retry após as demais completarem")
            else:
                r = ResultadoCotacao(
                    transportadora=nome_task, status="erro", detalhes=str(detalhe),
                )
                concluidas += 1
                resultados.append(r)
                _emitir_progresso(concluidas=concluidas, total=total_cotacoes, resultado=r)

    # ── Rodada 1: executa todas as cotações ──
    falhas_para_retry: list[tuple[str, Any, dict[str, Any]]] = []
    futuros = []
    for i, (nome, prov, kwargs) in enumerate(tasks):
        t = asyncio.ensure_future(_exec(i, nome, prov, kwargs))
        futuros.append(t)

    for fut in asyncio.as_completed(futuros):
        try:
            res = await fut
            _processar_resultado(res, resultados, falhas_para_retry)
        except Exception as loop_exc:
            import traceback
            tb = ''.join(traceback.format_exception(type(loop_exc), loop_exc, loop_exc.__traceback__))
            _log_diag(f"Falha ao processar resultado de cotação: {loop_exc}\n{tb}")
            concluidas += 1
            r = ResultadoCotacao(
                transportadora="GERAL", status="erro",
                detalhes=f"Falha interna ao processar cotação: {loop_exc}",
            )
            resultados.append(r)
            _emitir_progresso(concluidas=concluidas, total=total_cotacoes, resultado=r)

    # ── Rodada 2: retry das que falharam (máx 1 retry, sem enfileirar de novo) ──
    if falhas_para_retry:
        nomes_retry = ", ".join(n for n, _, _ in falhas_para_retry)
        total_cotacoes += len(falhas_para_retry)
        _log_diag(f"Retentando {len(falhas_para_retry)} cotação(ões) que falharam: {nomes_retry}")
        _emitir_progresso(concluidas=concluidas, total=total_cotacoes)

        futuros_retry = []
        for i, (nome, prov, kwargs) in enumerate(falhas_para_retry):
            t = asyncio.ensure_future(_exec(i, nome, prov, kwargs))
            futuros_retry.append(t)

        for fut in asyncio.as_completed(futuros_retry):
            try:
                res = await fut
                _processar_resultado(res, resultados, None)  # None = não enfileira de novo
            except Exception as loop_exc:
                import traceback
                tb = ''.join(traceback.format_exception(type(loop_exc), loop_exc, loop_exc.__traceback__))
                _log_diag(f"Falha ao processar retry de cotação: {loop_exc}\n{tb}")
                concluidas += 1
                r = ResultadoCotacao(
                    transportadora="GERAL", status="erro",
                    detalhes=f"Falha interna no retry: {loop_exc}",
                )
                resultados.append(r)
                _emitir_progresso(concluidas=concluidas, total=total_cotacoes, resultado=r)

    validas = [r for r in resultados if r.status == "ok" and r.valor_frete is not None]
    _log_diag(f"Cotações válidas: {len(validas)} de {len(tasks)}")

    return resultados


async def cotar_transportadoras(
    *,
    extrator,
    pedidos: list[Any],
    cep_origem: str = "",
    config_path: Path | None = None,
    sessao: TransportadoraSession | None = None,
    progresso_callback: "Callable[[dict[str, Any]], None] | None" = None,
) -> list[ResultadoCotacao]:
    """Executa cotação em todas as transportadoras configuradas."""
    config = sessao.config if sessao else _carregar_config(config_path=config_path)
    dados = _dados_envio(extrator=extrator, pedidos=pedidos)
    if not dados:
        _log_diag("Sem dados de envio para cotação")
        return [ResultadoCotacao(transportadora="GERAL", status="erro", detalhes="Nenhum pedido disponível para cotação")]
    return await _executar_cotacoes_com_dados(
        config=config,
        dados=dados,
        cep_origem=cep_origem,
        sessao=sessao,
        progresso_callback=progresso_callback,
    )


async def cotar_transportadoras_romaneio_colado(
    *,
    romaneio_colado: str,
    cep_origem: str = "",
    config_path: Path | None = None,
    sessao: "TransportadoraSession | None" = None,
    progresso_callback: "Callable[[dict[str, Any]], None] | None" = None,
    cnpj_remetente: str = "",
    tipo_frete: str = "",
) -> list[ResultadoCotacao]:
    config = sessao.config if sessao else _carregar_config(config_path=config_path)
    try:
        dados = _dados_envio_romaneio_colado(romaneio_colado)
    except ValueError as e:
        _log_diag(f"Romaneio colado inválido: {e}")
        return [ResultadoCotacao(transportadora="GERAL", status="erro", detalhes=str(e))]
    return await _executar_cotacoes_com_dados(
        config=config,
        dados=dados,
        cep_origem=cep_origem,
        sessao=sessao,
        progresso_callback=progresso_callback,
        cnpj_remetente=cnpj_remetente,
        tipo_frete=tipo_frete,
    )


async def diagnosticar_transportadoras(
    *,
    destino_cep: str,
    cnpj_destinatario: str,
    peso: float,
    valor: float,
    volumes: int = 1,
    cep_origem: str = "",
    config_path: Path | None = None,
    progresso_callback: "Callable[[dict[str, Any]], None] | None" = None,
) -> list[ResultadoCotacao]:
    config = _carregar_config(config_path=config_path)
    dados = {
        "destino_cep": _cep(destino_cep),
        "cnpj_destinatario": _digits(cnpj_destinatario),
        "peso": float(peso),
        "valor": float(valor),
        "volumes": int(volumes or 1),
    }
    return await _executar_cotacoes_com_dados(
        config=config,
        dados=dados,
        cep_origem=cep_origem,
        progresso_callback=progresso_callback,
    )


def formatar_resultados_cotacao(resultados: list[ResultadoCotacao]) -> str:
    linhas: list[str] = []
    linhas.append("=== COTAÇÕES TRANSPORTADORAS ===")

    # Verificar erros de divergência CEP/UF (bloqueio)
    for r in resultados:
        if r.status == "erro_divergencia_uf":
            linhas.append(f"\n⚠️ COTAÇÃO BLOQUEADA:\n{r.detalhes}")
            return "\n".join(linhas)

    validas = sorted(
        [r for r in resultados if r.status == "ok" and r.valor_frete is not None],
        key=lambda r: (float(r.valor_frete or 0.0), int(r.prazo_dias or 0), r.transportadora),
    )
    for item in validas:
        linhas.append(
            f"- {item.transportadora}: R$ {item.valor_frete:.2f} | {item.prazo_dias} dia(s)"
        )

    if validas:
        melhor = validas[0]
        linhas.append("")
        linhas.append(f"Melhor frete: {melhor.transportadora} - R$ {melhor.valor_frete:.2f}")
    else:
        linhas.append("- Nenhuma cotação válida retornada")
        if _diag_log_enabled():
            linhas.append("- Diagnóstico: verifique o arquivo romaneio_cotacao.log")

    return "\n".join(linhas)


import traceback


def setup_global_exception_handler():
    """Configura um manipulador global de exceções para logar erros não tratados."""
    def handle_exception(exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        error_message = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
        _log_diag(f"Unhandled exception:\n{error_message}")
        try:
            import logging
            logging.getLogger("unhandled").critical("Exceção não tratada:\n%s", error_message)
        except Exception:
            pass

    sys.excepthook = handle_exception

    # Captura exceções em coroutines asyncio que não são awaited
    def _asyncio_exception_handler(loop, context):
        msg = context.get("message", "")
        exc = context.get("exception")
        details = f"asyncio exception: {msg}"
        if exc:
            details += f"\n{''.join(traceback.format_exception(type(exc), exc, exc.__traceback__))}"
        _log_diag(details)
        try:
            import logging
            logging.getLogger("asyncio").error(details)
        except Exception:
            pass

    try:
        import asyncio
        loop = asyncio.get_event_loop()
        loop.set_exception_handler(_asyncio_exception_handler)
    except Exception:
        pass


def formatar_resultados_diagnostico(resultados: list[ResultadoCotacao]) -> str:
    linhas: list[str] = []
    linhas.append("=== DIAGNÓSTICO DE COTAÇÕES ===")

    validas = sorted(
        [r for r in resultados if r.status == "ok" and r.valor_frete is not None],
        key=lambda r: (float(r.valor_frete or 0.0), int(r.prazo_dias or 0), r.transportadora),
    )
    invalidas = [r for r in resultados if not (r.status == "ok" and r.valor_frete is not None)]

    if validas:
        linhas.append("- Válidas:")
        for item in validas:
            linhas.append(
                f"  * {item.transportadora}: R$ {item.valor_frete:.2f} | {item.prazo_dias} dia(s)"
                + (f" | {item.detalhes}" if item.detalhes else "")
            )
    else:
        linhas.append("- Válidas: nenhuma")

    if invalidas:
        linhas.append("- Falhas/Não configuradas:")
        for item in invalidas:
            linhas.append(
                f"  * {item.transportadora}: {item.status}"
                + (f" | {item.detalhes}" if item.detalhes else "")
            )

    return "\n".join(linhas)
