"""Fretio — shell web (POC pywebview + WebView2).

Substitui a janela PySide6 por uma UI web (app/web/*) renderizada no WebView2,
mantendo TODO o backend Python intacto. Esta é a fatia vertical (Dashboard) que
valida a arquitetura antes de migrar as demais telas.

Pontes:
  JS  -> Python : métodos públicos de `Api` (pywebview.api.<metodo>)
  Python -> JS  : emit(window, evento, payload) -> window.onBackendEvent({...})

NÃO importa nem altera romaneio_app.py. Entrypoint paralelo, 100% reversível.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any

# ── sys.path (dev) ─────────────────────────────────────────────────────────
_APP_DIR = Path(__file__).resolve().parent
_FRETIO_SRC = _APP_DIR / "fretio" / "src"
for _p in (_APP_DIR, _FRETIO_SRC):
    _s = str(_p)
    if _s not in sys.path:
        sys.path.insert(0, _s)

import webview  # noqa: E402

import company_config as cc  # noqa: E402

try:
    import tomllib as _toml_reader  # py311+
except ModuleNotFoundError:  # pragma: no cover
    _toml_reader = None
    import toml as _toml_fallback


# ── Helpers de config/versão (reusam o backend real) ───────────────────────
def _load_config(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        if _toml_reader is not None:
            return _toml_reader.loads(raw)
        return _toml_fallback.loads(raw)
    except Exception:
        return {}


def _carregar_versao() -> str:
    candidates = [
        Path(getattr(sys, "_MEIPASS", "") or "") / "version.txt",
        _APP_DIR / "version.txt",
    ]
    for c in candidates:
        try:
            if c and c.exists():
                return c.read_text(encoding="utf-8").strip()
        except OSError:
            continue
    return ""


def _resolver_tema_efetivo(modo: str) -> str:
    modo = (modo or "sistema").lower()
    if modo == "claro":
        return "claro"
    if modo == "escuro":
        return "escuro"
    return "escuro"  # 'sistema' no POC assume a identidade escura padrão


def _index_path() -> str:
    base = Path(getattr(sys, "_MEIPASS", "") or _APP_DIR)
    return str(base / "web" / "index.html")


def _startup_path() -> str:
    base = Path(getattr(sys, "_MEIPASS", "") or _APP_DIR)
    return str(base / "web" / "startup.html")


# ── Push Python -> JS (única fronteira para a GUI, espelha o postEvent) ─────
def emit(window: Any, evento: str, payload: dict | None = None) -> None:
    if window is None:
        return
    msg = json.dumps({"event": evento, "payload": payload or {}}, ensure_ascii=False)
    # Strings vêm de portais de transportadora; escapar os caracteres que podem
    # encerrar o literal JS (U+2028/U+2029) ou um <script> embutido antes de
    # injetar em evaluate_js. Equivalentes dentro de string JSON, então o
    # contrato {event,payload} permanece idêntico.
    msg = (
        msg.replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )
    try:
        window.evaluate_js(f"window.onBackendEvent({msg})")
    except Exception:
        pass


# Campos de credenciais por transportadora — derivados do registro único
# (ProviderSpec.credential_fields em factory.py), preservando ordem e rótulos.
from fretio.providers.factory import _PROVIDER_SPECS  # noqa: E402

_CARRIER_FIELDS: dict[str, list[tuple[str, str, str]]] = {
    key: list(spec.credential_fields) for key, spec in _PROVIDER_SPECS.items()
}


def _nota_card(indice: int, nf: Any) -> dict:
    """Monta os dados de um card de NF-e (porta de _criar_card_nfe, só texto)."""
    import re
    from extrator_nfe import identificar_transportadora, parsear_info_complementar

    transp = identificar_transportadora(nf)
    transp_display = (nf.transportadora_nome or transp.upper() or "NÃO IDENTIFICADA")
    data_emissao_display = ""
    if nf.data_emissao:
        md = re.match(r"(\d{4})-(\d{2})-(\d{2})", nf.data_emissao)
        if md:
            data_emissao_display = f"  |  Emissão: {md.group(3)}/{md.group(2)}/{md.group(1)}"
    header = f"[{indice}] NF-e {nf.numero} — {transp_display}{data_emissao_display}"

    info = parsear_info_complementar(nf.info_complementar)

    def t(v: Any) -> str:
        return str(v or "").strip()

    def fdata(v: Any) -> str:
        m = re.match(r"(\d{4})-(\d{2})-(\d{2})", t(v))
        return f"{m.group(3)}/{m.group(2)}/{m.group(1)}" if m else t(v)

    def fcep(v: Any) -> str:
        d = "".join(c for c in str(v or "") if c.isdigit())
        return f"{d[:5]}-{d[5:]}" if len(d) == 8 else t(v)

    def linha(campos):
        return "  |  ".join(f"{r}: {t(v)}" for r, v in campos)

    def transp_bloco() -> str:
        if transp:
            return transp.upper()
        nome = t(nf.transportadora_nome)
        return (nome.split()[0] if nome else "").upper()

    pd_display = t(info.get("pd"))
    if not pd_display and info.get("pedido_venda"):
        mpd = re.search(r"\bPD\b\s*([A-Z0-9./-]+)", t(info.get("pedido_venda")), re.IGNORECASE)
        pd_display = mpd.group(1) if mpd else t(info.get("pedido_venda"))

    cidade_uf = t(info.get("cidade_uf_entrega"))
    if not cidade_uf and nf.destinatario_cidade and nf.destinatario_uf:
        cidade_uf = f"{nf.destinatario_cidade}/{nf.destinatario_uf}"
    dest = t(nf.destinatario_nome)
    if dest and nf.destinatario_uf and not dest.endswith(f"/{nf.destinatario_uf}"):
        dest = f"{dest}/{nf.destinatario_uf}"

    lic = [
        linha([("Processo", info.get("processo")), ("PE", info.get("pe")), ("Ata", info.get("ata")),
               ("Contrato", info.get("contrato")), ("Empenho", info.get("empenho")), ("OF", info.get("of"))]),
        linha([("Entrega", info.get("entrega")), ("Pagamento", info.get("pagamento"))]),
        dest,
        linha([("CRM", info.get("crm")), ("PD", pd_display)]),
        "",
        linha([("NOTA FISCAL", nf.numero), ("DATA NF", fdata(nf.data_emissao))]),
        f"PRODUTOS: {t(nf.produtos_resumo)}",
        linha([("TRANSPORTADORA", transp_bloco()), ("RASTREIO", "(NÃO PREENCHA)")]),
    ]
    if info.get("outras_info_licitacao"):
        lic += ["", "Outras informações da licitação:", t(info.get("outras_info_licitacao"))]

    entrega = [
        f"LOCAL DE ENTREGA: {t(info.get('local_entrega_nome'))}",
        f"ENDEREÇO: {t(info.get('endereco_entrega'))}",
        f"CEP: {fcep(info.get('cep_entrega') or nf.destinatario_cep)}",
        cidade_uf,
        "",
        f"AGENDAMENTO: {t(info.get('agendamento'))}",
        linha([("HORÁRIO", info.get("horario")),
               ("CONTATO", info.get("contato") or info.get("recebedor")),
               ("TELEFONE", info.get("telefone"))]),
    ]
    if info.get("outras_info_entrega"):
        entrega += ["", "Outras informações da entrega:", t(info.get("outras_info_entrega"))]

    return {
        "indice": indice,
        "header": header,
        "bloco_licitacao": "\n".join(lic).rstrip(),
        "bloco_entrega": "\n".join(entrega).rstrip(),
        "chave": getattr(nf, "chave_acesso", "") or f"nf-{indice}-{nf.numero}",
        "numero": nf.numero,
    }


def _validar_local_entrega(extrator: Any, pedidos: list) -> tuple[bool, str]:
    """Porta de RomaneioWindow._validar_local_entrega: detecta CEP ausente ou
    locais de entrega divergentes entre os pedidos do romaneio."""
    import re
    if not pedidos:
        return True, ""

    sem_cep = []
    for p in pedidos:
        local = getattr(p, "local_entrega", "") or ""
        cep = extrator.obter_cep_local_entrega(local) if hasattr(extrator, "obter_cep_local_entrega") else None
        if not cep:
            sem_cep.append(str(getattr(p, "numero", "?")))
    if sem_cep:
        return False, "CEP não encontrado nos pedidos: " + ", ".join(sem_cep)

    locais: dict[str, dict] = {}
    for p in pedidos:
        local = getattr(p, "local_entrega", "") or ""
        norm = (extrator.normalizar_local_entrega(local) or "").strip() if hasattr(extrator, "normalizar_local_entrega") else local.strip()
        if not norm or norm.upper() == "N/A":
            continue
        chave = extrator.chave_local_entrega(local) if hasattr(extrator, "chave_local_entrega") else re.sub(r"\s+", " ", norm).strip().upper()
        if not chave:
            continue
        locais.setdefault(chave, {"local": norm, "pedidos": []})["pedidos"].append(str(getattr(p, "numero", "?")))

    if len(locais) <= 1:
        return True, ""
    msg = "Locais de entrega diferentes encontrados:\n"
    for info in locais.values():
        msg += f"• {info['local'].split(chr(10))[0]} — pedidos: {', '.join(info['pedidos'])}\n"
    return False, msg.rstrip()


# ── Ponte JS -> Python ─────────────────────────────────────────────────────
class ConfigMixin:
    """Leitura/escrita de configuração e aparência.

    Delegate de domínio do god-object Api. Os métodos operam sobre o estado do
    Api (self._config_path / self._empresa) — a superfície pública pywebview
    permanece idêntica (ver test_char_web_app_api_surface.py)."""

    def set_tema(self, modo: str) -> dict[str, Any]:
        modo = (modo or "sistema").lower()
        if modo not in ("claro", "escuro", "sistema"):
            modo = "sistema"
        cfg = _load_config(self._config_path)
        if not isinstance(cfg.get("fretio"), dict):
            cfg["fretio"] = {}
        cfg["fretio"]["ui_tema"] = modo
        try:
            cc._escrever_config_toml(cfg, self._config_path)
            ok = True
        except Exception:
            ok = False
        return {"ok": ok, "tema": modo, "tema_efetivo": _resolver_tema_efetivo(modo)}

    def config_get(self) -> dict:
        cfg = _load_config(self._config_path)
        fb = cfg.get("fretio", {}) or {}
        rom = cfg.get("romaneio", {}) or {}
        transp = cfg.get("transportadoras", {}) or {}

        carriers = []
        for nome, fields in _CARRIER_FIELDS.items():
            sec = transp.get(nome, {}) or {}
            carriers.append({
                "nome": nome,
                "habilitado": bool(sec.get("habilitado", False)),
                "ufs_atendidas": [str(x).upper() for x in (sec.get("ufs_atendidas", []) or [])],
                "campos": [
                    {
                        "key": k, "label": lbl, "tipo": tp,
                        "valor": "" if tp == "password" else str(sec.get(k, "") or ""),
                        "tem_valor": bool(str(sec.get(k, "") or "")),
                    }
                    for k, lbl, tp in fields
                ],
            })

        try:
            paralelas = int(fb.get("max_paralelo", 3) or 3)
        except (TypeError, ValueError):
            paralelas = 3
        return {
            "empresa": {
                "nome": self._empresa,
                "cep_origem": str(rom.get("cep_origem", "") or ""),
                "cnpj_pagador": str(rom.get("cnpj_pagador_padrao", "") or ""),
                "paralelas": paralelas,
            },
            "aparencia": {
                "tema": str(fb.get("ui_tema", "sistema")).lower(),
                "raio": str(fb.get("ui_raio", "Suave")),
                "botao": str(fb.get("ui_botao", "Solido")),
                "accent": str(fb.get("ui_accent", "Claude")),
                "temas": ["claro", "escuro", "sistema"],
                "raios": ["Reto", "Suave", "Arredondado"],
                "botoes": ["Solido", "Suave"],
            },
            "transportadoras": carriers,
            "ufs": list(cc.TODAS_UFS),
        }

    def _write_config(self, mutate) -> bool:
        cfg = _load_config(self._config_path)
        try:
            mutate(cfg)
            cc._escrever_config_toml(cfg, self._config_path)
            return True
        except Exception:
            return False

    def config_salvar_empresa(self, data: dict) -> dict:
        import re
        data = data or {}

        def mut(cfg):
            rom = cfg.setdefault("romaneio", {})
            cep = re.sub(r"\D", "", str(data.get("cep_origem", "")))
            rom["cep_origem"] = cep
            rom["cnpj_pagador_padrao"] = str(data.get("cnpj_pagador", "")).strip()
            fb = cfg.setdefault("fretio", {})
            try:
                fb["max_paralelo"] = max(1, min(7, int(str(data.get("paralelas", "3")).strip() or "3")))
            except (TypeError, ValueError):
                fb["max_paralelo"] = 3

        return {"ok": self._write_config(mut)}

    def config_salvar_aparencia(self, data: dict) -> dict:
        data = data or {}
        tema = str(data.get("tema", "sistema")).lower()
        if tema not in ("claro", "escuro", "sistema"):
            tema = "sistema"

        def mut(cfg):
            fb = cfg.setdefault("fretio", {})
            fb["ui_tema"] = tema
            if data.get("raio"):
                fb["ui_raio"] = str(data["raio"])
            if data.get("botao"):
                fb["ui_botao"] = str(data["botao"])

        ok = self._write_config(mut)
        return {
            "ok": ok, "tema_efetivo": _resolver_tema_efetivo(tema),
            "raio": data.get("raio"), "botao": data.get("botao"),
        }

    def config_salvar_transportadora(self, nome: str, data: dict) -> dict:
        nome = str(nome)
        data = data or {}

        def mut(cfg):
            t = cfg.setdefault("transportadoras", {}).setdefault(nome, {})
            if "habilitado" in data:
                t["habilitado"] = bool(data["habilitado"])
            if "ufs_atendidas" in data:
                t["ufs_atendidas"] = [str(x).upper() for x in (data.get("ufs_atendidas") or [])]

        return {"ok": self._write_config(mut)}

    def config_salvar_credenciais(self, nome: str, campos: dict) -> dict:
        nome = str(nome)
        allowed = {k for k, _, _ in _CARRIER_FIELDS.get(nome, [])}
        senha_keys = {k for k, _, tp in _CARRIER_FIELDS.get(nome, []) if tp == "password"}

        def mut(cfg):
            t = cfg.setdefault("transportadoras", {}).setdefault(nome, {})
            for k, v in (campos or {}).items():
                if k not in allowed:
                    continue
                v = str(v)
                # Senha em branco = manter a já salva (a UI nunca recebe a senha).
                if k in senha_keys and v == "":
                    continue
                t[k] = v

        return {"ok": self._write_config(mut)}


class StartupMixin:
    """Partida: licença, versão/update e seleção/criação de empresa.

    Delegate de domínio do god-object Api. Opera sobre o estado do Api (self) —
    superfície pública pywebview inalterada (test_char_web_app_api_surface.py)."""

    def startup_licenca_estado(self) -> dict:
        from license import get_saved_license, get_machine_id, validate_license
        key = get_saved_license()
        machine = get_machine_id()
        if not key:
            return {"fase": "pedir_chave", "msg": ""}
        status = validate_license(key, machine)
        if status.valid:
            try:
                from usage_reporter import report_license_validated
                report_license_validated("ok")
            except Exception:
                pass
            return {"fase": "ok", "msg": ""}
        return {"fase": "revogada", "msg": status.message or "Sua licença não é mais válida."}

    def startup_ativar_licenca(self, key: str) -> dict:
        from license import get_machine_id, validate_license, save_license
        key = str(key or "").strip().upper()
        if not key:
            return {"ok": False, "msg": "Informe a chave de licença."}
        status = validate_license(key, get_machine_id())
        if status.valid:
            save_license(key)
            try:
                from usage_reporter import report_license_validated
                report_license_validated("ok")
            except Exception:
                pass
            return {"ok": True, "msg": ""}
        return {"ok": False, "msg": status.message or "Chave não reconhecida."}

    def startup_pos_licenca(self) -> dict:
        from startup import _fetch_remote_config_sync, _carregar_versao_app
        from version_policy import evaluate_minimum_version
        from updater import get_repo_from_config, check_for_update

        cur = _carregar_versao_app()
        repo = get_repo_from_config()
        payload = _fetch_remote_config_sync()
        policy = evaluate_minimum_version((payload or {}).get("config", {}), cur)

        info = None
        try:
            info = check_for_update(repo, cur) if repo else None
        except Exception:
            info = None
        self._update_info = info
        upd = None
        if info:
            upd = {
                "version": getattr(info, "version", ""),
                "notes": str(getattr(info, "release_notes", "") or "")[:1500],
                "mandatory": bool(getattr(info, "mandatory", False)),
            }

        bloqueado = bool(policy.is_outdated and policy.should_block)
        msg = ""
        if bloqueado:
            msg = (f"Sua versão (v{policy.current_version}) não é compatível com o servidor.\n"
                   f"Versão mínima: v{policy.min_app_version or 'desconhecida'}. Atualize para continuar.")
        return {"bloqueado": bloqueado, "msg": msg, "update": upd}

    def startup_aplicar_update(self) -> dict:
        info = self._update_info
        if not info:
            return {"ok": False, "erro": "Nenhuma atualização disponível"}
        from updater import apply_update, restart_app
        try:
            ok = apply_update(info, callback=lambda m: self._emit("startup_progress", {"texto": str(m or "")}))
        except Exception as exc:
            return {"ok": False, "erro": str(exc)}
        if ok:
            try:
                restart_app()
            except Exception:
                pass
            return {"ok": True}
        return {"ok": False, "erro": "Não foi possível aplicar a atualização agora."}

    def startup_empresas(self) -> list:
        return cc._listar_empresas()

    def startup_criar_empresa(self, nome: str) -> dict:
        nome = str(nome or "").strip()
        if not nome:
            return {"ok": False, "erro": "Informe um nome para a empresa."}
        if nome in cc._listar_empresas():
            return {"ok": False, "erro": "Já existe uma empresa com esse nome."}
        try:
            cc._criar_config_empresa_vazia(nome)
            return {"ok": True}
        except Exception as exc:
            return {"ok": False, "erro": str(exc)}

    def startup_renomear_empresa(self, atual: str, novo: str) -> dict:
        atual, novo = str(atual or "").strip(), str(novo or "").strip()
        if not atual or not novo:
            return {"ok": False, "erro": "Nome inválido."}
        try:
            ok = cc._renomear_pasta_empresa(atual, novo)
            return {"ok": bool(ok), "erro": "" if ok else "Não foi possível renomear."}
        except Exception as exc:
            return {"ok": False, "erro": str(exc)}

    def startup_entrar(self, empresa: str) -> dict:
        empresa = str(empresa or "").strip()
        if not empresa:
            return {"ok": False, "erro": "Empresa inválida."}
        self._empresa = empresa
        self._config_path = cc._empresa_config_path(empresa)
        self._versao = _carregar_versao()
        try:
            cc._salvar_ultima_empresa(empresa)
        except Exception:
            pass
        try:
            from error_reporter import configure as _er_configure
            _er_configure(self._config_path)
        except Exception:
            pass
        try:
            cfg = _load_config(self._config_path)
            changed = cc._garantir_defaults_fretio(cfg)
            changed = cc._garantir_defaults_empresa(cfg) or changed
            if changed:
                cc._escrever_config_toml(cfg, self._config_path)
        except Exception:
            pass
        return {"ok": True}


class Api(ConfigMixin, StartupMixin):
    def __init__(self, empresa: str | None = None, config_path: Path | None = None) -> None:
        self._empresa = empresa or ""
        self._config_path = config_path
        self._versao = _carregar_versao()
        self._window: Any = None
        self._update_info: Any = None
        # Controlador de backend (lazy — só criado quando cotação/rastreio são usados).
        self._sessao: Any = None
        self._loop: Any = None
        self._cotando = False
        self._notas: list[Any] = []  # NotaFiscal carregadas (rastreio)
        self._romaneios: list[dict] = []  # romaneios processados na sessão (dashboard)
        self._last_cotacao: list[Any] = []  # últimos ResultadoCotacao (dashboard)
        self._romaneio_texto = ""  # último romaneio calculado (handoff p/ cotação)

    def attach_window(self, window: Any) -> None:
        self._window = window
        # Fechar a janela (X ou sair()) encerra worker + sessão Playwright.
        try:
            window.events.closed += self._teardown
        except Exception:
            pass

    def _teardown(self) -> None:
        # Encerra o worker assíncrono e a sessão Playwright (fecha o Chrome do
        # Rodonaves e libera o user-data-dir). Sem isso o processo do Chrome vaza
        # ao fechar a janela e o lock do perfil pode corromper a próxima abertura.
        # Idempotente: zera _loop/_sessao antes, então chamadas repetidas são no-op.
        loop, sessao = self._loop, self._sessao
        self._loop = None
        self._sessao = None
        if loop is not None:
            try:
                loop.shutdown(cleanup_coro_factory=(sessao.cleanup if sessao is not None else None))
            except Exception:
                pass

    def _emit(self, evento: str, payload: dict | None = None) -> None:
        emit(self._window, evento, payload)

    def _gate(self, feature: str) -> dict | None:
        """Gating por licença/config remota (fail-open). Retorna erro se bloqueado."""
        try:
            from remote_permissions import feature_allowed_or_default, feature_message
            if not feature_allowed_or_default(feature):
                return {"erro": feature_message(feature)}
        except Exception:
            pass
        return None

    # --- leitura ---
    def get_bootstrap(self) -> dict[str, Any]:
        cfg = _load_config(self._config_path)
        fretio = cfg.get("fretio", {}) if isinstance(cfg.get("fretio"), dict) else {}
        tema = str(fretio.get("ui_tema", "sistema")).lower()

        transportadoras = []
        tcfg = cfg.get("transportadoras", {})
        if isinstance(tcfg, dict):
            for nome, sec in tcfg.items():
                if isinstance(sec, dict):
                    transportadoras.append({
                        "nome": nome,
                        "habilitado": bool(sec.get("habilitado", False)),
                        "status": "pending",
                    })

        # Dashboard é estado de sessão (em memória); no POC inicia vazio,
        # espelhando exatamente o app atual recém-aberto.
        dashboard = {
            "total_romaneios": 0,
            "total_volumes": 0,
            "melhor_frete": None,
            "sucesso_pct": None,
            "romaneios_recentes": [],
        }
        return {
            "empresa": self._empresa,
            "versao": self._versao,
            "tema": tema,
            "tema_efetivo": _resolver_tema_efetivo(tema),
            "raio": str(fretio.get("ui_raio", "Suave")),
            "botao": str(fretio.get("ui_botao", "Solido")),
            "transportadoras": transportadoras,
            "dashboard": dashboard,
        }

    def listar_empresas(self) -> list[str]:
        return cc._listar_empresas()

    # ── Cotação ────────────────────────────────────────────────────────────
    def _ensure_backend(self) -> None:
        if self._sessao is not None:
            return
        from cotacao_transportadoras import TransportadoraSession  # import pesado, lazy
        from async_worker import AsyncWorkerLoop

        self._sessao = TransportadoraSession(config_path=self._config_path)
        self._loop = AsyncWorkerLoop(name="WebAsyncLoop")

    @staticmethod
    def _num(v: Any) -> Any:
        if v is None:
            return None
        try:
            f = float(v)
            return int(f) if f.is_integer() else f
        except (TypeError, ValueError):
            return None

    def _ser_resultado(self, r: Any) -> dict | None:
        if r is None:
            return None
        return {
            "status": getattr(r, "status", None),
            "valor_frete": self._num(getattr(r, "valor_frete", None)),
            "prazo_dias": self._num(getattr(r, "prazo_dias", None)),
            "transportadora": getattr(r, "transportadora", None),
            "detalhes": getattr(r, "detalhes", None),
            "duration_ms": self._num(getattr(r, "duration_ms", None)),
        }

    def _cb_progress(self, payload: dict) -> None:
        p = dict(payload or {})
        self._emit("cotacao_progress", {
            "provider": p.get("provider"),
            "status": p.get("status"),
            "stage": p.get("stage"),
            "mensagem": p.get("mensagem"),
            "duration_ms": self._num(p.get("duration_ms")),
            "resultado": self._ser_resultado(p.get("resultado")),
        })

    def _cb_status(self, msg: str) -> None:
        self._emit("status_update", {"texto": str(msg or "")})

    def _cb_login(self, nome: str, status: str) -> None:
        self._emit("login_status", {"nome": str(nome or ""), "status": str(status or "")})

    def cotacao_iniciar(self, romaneio_texto: str, cnpj_remetente: str = "", cep_origem: str = "") -> dict:
        texto = str(romaneio_texto or "").strip()
        if not texto:
            return {"erro": "Cole um romaneio antes de cotar"}
        bloqueio = self._gate("cotacao")
        if bloqueio:
            return bloqueio
        if self._cotando:
            return {"erro": "Já existe uma cotação em andamento"}
        try:
            self._ensure_backend()
        except Exception as exc:  # backend indisponível
            return {"erro": f"Falha ao preparar a cotação: {exc}"}

        self._cotando = True
        fut = self._loop.submit(lambda: self._coro_cotacao(texto, cnpj_remetente, cep_origem))
        if fut is None:
            self._cotando = False
            return {"erro": "Não foi possível iniciar a cotação"}
        return {"ok": True}

    async def _coro_cotacao(self, texto: str, cnpj_remetente: str, cep_origem: str) -> None:
        from cotacao_transportadoras import (
            cotar_transportadoras_romaneio_colado,
            formatar_resultados_cotacao,
            CHROME_MISSING_USER_MESSAGE,
        )
        try:
            if not self._sessao.pronto:
                self._emit("status_update", {"texto": "Executando pré-login antes da cotação..."})
                await self._sessao.inicializar(
                    callback=self._cb_status,
                    login_status_callback=self._cb_login,
                )
                if self._sessao.chrome_missing:
                    self._emit("cotacao_result", {"resumo": CHROME_MISSING_USER_MESSAGE})
                    self._emit("chrome_missing", {"texto": CHROME_MISSING_USER_MESSAGE})
                    return

            kwargs: dict[str, Any] = dict(
                romaneio_colado=texto,
                cep_origem=cep_origem or "",
                sessao=self._sessao,
                progresso_callback=self._cb_progress,
            )
            if cnpj_remetente:
                kwargs["cnpj_remetente"] = cnpj_remetente
                kwargs["tipo_frete"] = "2"  # FOB (modo fornecedor)
            resultados = await cotar_transportadoras_romaneio_colado(**kwargs)
            self._last_cotacao = list(resultados or [])
            resumo = formatar_resultados_cotacao(resultados)
            self._emit("cotacao_result", {"resumo": resumo})
            self._emit("dashboard_dirty", {})
        except Exception as exc:  # falha técnica
            self._emit("cotacao_result", {
                "resumo": "Erro ao cotar transportadoras. Tente novamente em alguns minutos.",
            })
            self._emit("toast", {"texto": f"Erro na cotação: {type(exc).__name__}"})
        finally:
            self._cotando = False
            self._emit("cotacao_finished", {})

    # ── Rastreio / NF-e ─────────────────────────────────────────────────────
    def nfe_selecionar(self) -> dict:
        if self._window is None:
            return {"erro": "Janela indisponível"}
        bloqueio = self._gate("nfe")
        if bloqueio:
            return bloqueio
        paths = self._window.create_file_dialog(
            webview.OPEN_DIALOG,
            allow_multiple=True,
            file_types=("XML NF-e (*.xml)", "Todos os arquivos (*.*)"),
        )
        if not paths:
            return {"cancelado": True}
        from extrator_nfe import extrair_arquivo as _extrair

        erros: list[str] = []
        novas: list[Any] = []
        vistos = {getattr(n, "chave_acesso", "") for n in self._notas if getattr(n, "chave_acesso", "")}
        for arq in paths:
            nome = Path(arq).name
            try:
                notas = _extrair(arq)
                if not notas:
                    erros.append(f"{nome}: nenhuma NF-e encontrada")
                    continue
                for nf in notas:
                    if nf.chave_acesso and nf.chave_acesso in vistos:
                        continue
                    if nf.chave_acesso:
                        vistos.add(nf.chave_acesso)
                    novas.append(nf)
            except Exception as exc:
                erros.append(f"{nome}: {exc}")

        base = len(self._notas)
        self._notas.extend(novas)
        cards = [_nota_card(base + i + 1, nf) for i, nf in enumerate(novas)]
        return {"cards": cards, "erros": erros, "total_notas": len(self._notas)}

    def rastreio_limpar(self) -> dict:
        self._notas = []
        return {"ok": True}

    def _ser_rastreio(self, r: Any) -> dict | None:
        if r is None:
            return None
        return {
            "numero_nfe": getattr(r, "numero_nfe", ""),
            "transportadora": getattr(r, "transportadora", ""),
            "entregue": bool(getattr(r, "entregue", False)),
            "previsao_entrega": getattr(r, "previsao_entrega", ""),
            "link_rastreio": getattr(r, "link_rastreio", ""),
            "screenshot_path": getattr(r, "screenshot_path", ""),
            "status_texto": getattr(r, "status_texto", ""),
            "erro": getattr(r, "erro", ""),
        }

    def rastreio_iniciar(self, chaves: list | None = None) -> dict:
        if not self._notas:
            return {"erro": "Nenhuma NF-e carregada para rastrear"}
        bloqueio = self._gate("rastreio")
        if bloqueio:
            return bloqueio
        try:
            self._ensure_backend()
        except Exception as exc:
            return {"erro": f"Falha ao preparar o rastreamento: {exc}"}
        try:
            from fretio.providers.base import find_chrome
            find_chrome()
        except FileNotFoundError:
            from cotacao_transportadoras import CHROME_MISSING_USER_MESSAGE
            self._emit("chrome_missing", {"texto": CHROME_MISSING_USER_MESSAGE})
            return {"erro": CHROME_MISSING_USER_MESSAGE}

        alvo = self._notas
        if chaves:
            cset = set(chaves)
            sub = [n for n in self._notas if (getattr(n, "chave_acesso", "") in cset)]
            if sub:
                alvo = sub
        fut = self._loop.submit(lambda: self._coro_rastreio(list(alvo)))
        if fut is None:
            return {"erro": "Não foi possível iniciar o rastreamento"}
        return {"ok": True, "total": len(alvo)}

    async def _coro_rastreio(self, alvo: list) -> None:
        from rastreamento import rastrear_multiplas
        from extrator_nfe import identificar_transportadora

        notas_track: list[dict] = []
        chaves: list[str] = []
        for nf in alvo:
            notas_track.append({
                "transportadora": identificar_transportadora(nf),
                "numero_nfe": nf.numero,
                "cnpj_emitente": nf.emitente_cnpj,
                "chave_acesso": nf.chave_acesso,
            })
            chaves.append(getattr(nf, "chave_acesso", "") or f"nf-{nf.numero}")

        def _cb(indice: int, total: int, resultado: Any) -> None:
            chave = chaves[indice - 1] if 0 <= indice - 1 < len(chaves) else ""
            self._emit("rastreio_progress", {
                "chave": chave, "indice": indice, "total": total,
                "resultado": self._ser_rastreio(resultado),
            })

        try:
            resultados = await rastrear_multiplas(notas_track, callback=_cb)
            entregues = sum(1 for r in resultados if getattr(r, "entregue", False))
            com_ss = sum(1 for r in resultados if getattr(r, "screenshot_path", ""))
            self._emit("rastreio_finished", {
                "total": len(resultados), "entregues": entregues, "screenshots": com_ss,
            })
        except Exception as exc:
            self._emit("rastreio_finished", {
                "total": 0, "entregues": 0, "screenshots": 0, "erro": str(exc),
            })

    def abrir_externo(self, alvo: str) -> dict:
        """Abre arquivo (screenshot) ou URL (rastreio) no app padrão do sistema."""
        alvo = str(alvo or "").strip()
        if not alvo:
            return {"erro": "alvo vazio"}
        try:
            if sys.platform == "win32" and not alvo.lower().startswith(("http://", "https://")):
                os.startfile(alvo)  # type: ignore[attr-defined]
            else:
                import webbrowser
                webbrowser.open(alvo)
            return {"ok": True}
        except Exception as exc:
            return {"erro": str(exc)}

    def abrir_screenshots(self) -> dict:
        try:
            from rastreamento import _download_dir
            d = str(_download_dir())
            if sys.platform == "win32":
                os.startfile(d)  # type: ignore[attr-defined]
            else:
                import webbrowser
                webbrowser.open(d)
            return {"ok": True}
        except Exception as exc:
            return {"erro": str(exc)}

    # ── Dashboard (estado de sessão) ────────────────────────────────────────
    def get_dashboard(self) -> dict:
        roms = self._romaneios
        total_rom = len(roms)
        total_vol = sum(int(r.get("volumes", 0) or 0) for r in roms)
        ok = [
            r for r in self._last_cotacao
            if getattr(r, "status", "") == "ok" and getattr(r, "valor_frete", None) is not None
        ]
        melhor = min((float(r.valor_frete) for r in ok), default=None) if ok else None
        total_cot = len(self._last_cotacao)
        ok_cot = len(ok)
        return {
            "total_romaneios": total_rom,
            "total_volumes": total_vol,
            "melhor_frete": melhor,
            "sucesso_pct": round(ok_cot / total_cot * 100) if total_cot else None,
            "sub_romaneios": "processado nesta sessão" if total_rom == 1 else "processados nesta sessão",
            "sub_volumes": "volume processado" if total_vol == 1 else "volumes processados",
            "sub_sucesso": f"{ok_cot} de {total_cot} transportadoras" if total_cot else "",
            "romaneios_recentes": list(reversed(roms))[:6],
        }

    # ── Romaneio (PDF) ──────────────────────────────────────────────────────
    def romaneio_processar(self) -> dict:
        if self._window is None:
            return {"erro": "Janela indisponível"}
        bloqueio = self._gate("romaneio")
        if bloqueio:
            return bloqueio
        paths = self._window.create_file_dialog(
            webview.OPEN_DIALOG,
            allow_multiple=False,
            file_types=("PDF de romaneio (*.pdf)", "Todos os arquivos (*.*)"),
        )
        if not paths:
            return {"cancelado": True}
        arq = paths[0] if isinstance(paths, (list, tuple)) else paths

        from extrator_pedidos import ExtratorPedidos
        extrator = ExtratorPedidos()
        try:
            pedidos = extrator.extrair_arquivo(arq)
        except Exception as exc:
            return {"erro": f"Erro ao processar PDF: {exc}"}
        if not pedidos:
            return {"erro": "Nenhum pedido encontrado no arquivo. Verifique se o PDF tem o formato esperado."}

        ok, msg = _validar_local_entrega(extrator, pedidos)
        if not ok:
            return {"erro": msg}

        try:
            if len(pedidos) == 1:
                html = extrator.formatar_pedido_html(pedidos[0])
            else:
                html = extrator.formatar_pedidos_agrupados_html(pedidos)
        except ValueError as exc:
            return {"erro": str(exc)}

        texto = html.replace("<br>", "\n")
        destino = "—"
        try:
            d0 = getattr(pedidos[0], "local_entrega", "") or ""
            norm = extrator.normalizar_local_entrega(d0) if hasattr(extrator, "normalizar_local_entrega") else d0
            destino = (norm or d0 or "—").split("\n")[0] or "—"
        except Exception:
            pass

        from datetime import date
        self._romaneios.append({
            "data": date.today().strftime("%d/%m"),
            "nome": Path(arq).name,
            "destino": destino,
            "volumes": len(pedidos),
        })
        self._romaneio_texto = texto
        return {
            "ok": True, "texto": texto, "arquivo": Path(arq).name,
            "pedidos": len(pedidos), "destino": destino,
        }

    def get_romaneio_texto(self) -> dict:
        return {"texto": self._romaneio_texto}

    # ── Fornecedores (frete FOB) ────────────────────────────────────────────
    @staticmethod
    def _obter_cnpj_empresa(cfg: dict) -> str:
        import re
        transp = cfg.get("transportadoras", {}) or {}
        cnpj = re.sub(r"\D", "", str((transp.get("braspress") or {}).get("cnpj", "") or ""))
        if len(cnpj) == 14:
            return cnpj
        agex = transp.get("agex") or {}
        for chave in ("cnpj_remetente", "cnpj"):
            cnpj = re.sub(r"\D", "", str(agex.get(chave, "") or ""))
            if len(cnpj) == 14:
                return cnpj
        cnpj = re.sub(r"\D", "", str((transp.get("rodonaves") or {}).get("cnpj_pagador", "") or ""))
        return cnpj if len(cnpj) == 14 else ""

    @staticmethod
    def _obter_cep_empresa(cfg: dict) -> str:
        import re
        rom = cfg.get("romaneio", {}) or {}
        cep = re.sub(r"\D", "", str(rom.get("cep_origem", "") or ""))
        return cep if len(cep) == 8 else ""

    def _montar_romaneio_fornecedor(self, form: dict) -> tuple[str, str]:
        """Porta de CotacaoMixin._montar_romaneio_fornecedor (lê de um dict do form web)."""
        import re
        cfg = _load_config(self._config_path)
        cnpj_empresa = self._obter_cnpj_empresa(cfg)
        cep_empresa = self._obter_cep_empresa(cfg)
        cep_forn = re.sub(r"\D", "", str(form.get("cep", "")))

        def fbr(txt: Any) -> float:
            t = re.sub(r"[R$\s]", "", str(txt or "").strip())
            t = t.replace(".", "").replace(",", ".")
            return float(t) if t else 0.0

        try:
            qtd = int(str(form.get("qtd", "")).strip() or "0")
        except ValueError:
            qtd = 0
        alt, larg, comp = fbr(form.get("alt")), fbr(form.get("larg")), fbr(form.get("comp"))
        peso_cx_txt = str(form.get("peso_cx", "")).strip()
        peso_total_txt = str(form.get("peso_total", "")).strip()
        valor = fbr(form.get("valor"))

        if peso_cx_txt:
            peso_caixa = fbr(peso_cx_txt)
            peso_total = peso_caixa * qtd
        elif peso_total_txt:
            peso_total = fbr(peso_total_txt)
            peso_caixa = peso_total / qtd if qtd > 0 else 0.0
        else:
            raise ValueError("Informe o peso por volume ou o peso total (pelo menos um é obrigatório)")

        cubagem_unit = (alt * larg * comp) / 1_000_000
        cubagem_total = cubagem_unit * qtd

        erros: list[str] = []
        if len(cnpj_empresa) != 14:
            erros.append("CNPJ da empresa não configurado (Configurações > Credenciais)")
        if len(cep_empresa) != 8:
            erros.append("CEP da empresa não configurado (Configurações > Empresa > CEP de origem)")
        if len(cep_forn) != 8:
            erros.append("CEP do fornecedor inválido (deve ter 8 dígitos)")
        if qtd <= 0:
            erros.append("Quantidade de volumes deve ser maior que zero")
        if alt <= 0 or larg <= 0 or comp <= 0:
            erros.append("Dimensões devem ser maiores que zero")
        if peso_total <= 0:
            erros.append("Peso deve ser maior que zero")
        if erros:
            raise ValueError("\n".join(erros))

        c = cnpj_empresa
        cnpj_fmt = f"{c[:2]}.{c[2:5]}.{c[5:8]}/{c[8:12]}-{c[12:]}"
        cep_fmt = f"{cep_empresa[:5]}-{cep_empresa[5:]}"
        lines = [
            f"CNPJ/CPF: {cnpj_fmt}",
            f"CEP: {cep_fmt}",
            f"- VOL: {qtd}",
            f"- CUBAGEM: {cubagem_total:.6f} m3",
            f"- PESO: {peso_total:.2f} kg",
            f"- TOTAL: R$ {valor:.2f}",
            f"{qtd} x Volume fornecedor - {peso_caixa:.3f} kg - {cubagem_unit:.6f} m3 - {int(alt)}x{int(larg)}x{int(comp)}",
        ]
        return "\n".join(lines), cep_forn

    def fornecedor_cotar(self, form: dict) -> dict:
        import re
        bloqueio = self._gate("cotacao")
        if bloqueio:
            return bloqueio
        try:
            texto, cep_forn = self._montar_romaneio_fornecedor(form or {})
        except ValueError as exc:
            return {"erro": str(exc)}
        cnpj_forn = re.sub(r"\D", "", str((form or {}).get("cnpj", "")))
        return self.cotacao_iniciar(texto, cnpj_remetente=cnpj_forn, cep_origem=cep_forn)

    def abrir_app(self) -> dict:
        if self._window is not None:
            try:
                self._window.load_url(_index_path())
            except Exception as exc:
                return {"ok": False, "erro": str(exc)}
        return {"ok": True}

    def trocar_empresa(self) -> dict:
        """Volta para o seletor de empresa (recarrega a tela de partida)."""
        if self._window is not None:
            try:
                self._window.load_url(_startup_path() + "?fase=empresa")
            except Exception as exc:
                return {"ok": False, "erro": str(exc)}
        return {"ok": True}

    def sair(self) -> dict:
        try:
            self._teardown()
        except Exception:
            pass
        try:
            if self._window is not None:
                self._window.destroy()
        except Exception:
            pass
        return {"ok": True}


# ── Demo do canal de push (prova Python->JS a partir de background) ─────────
def _demo_pushes(api: Api) -> None:
    win = api._window
    time.sleep(0.9)
    emit(win, "status_update", {"texto": "Conectando às transportadoras…"})
    cfg = _load_config(api._config_path)
    tcfg = cfg.get("transportadoras", {}) if isinstance(cfg.get("transportadoras"), dict) else {}
    habilitadas = [n for n, s in tcfg.items() if isinstance(s, dict) and s.get("habilitado")]
    for nome in habilitadas:
        time.sleep(0.5)
        emit(win, "login_status", {"nome": nome, "status": "ok"})
    time.sleep(0.6)
    emit(win, "status_update", {"texto": "Nenhum arquivo carregado"})
    emit(win, "toast", {"texto": "Ponte Python→JS funcionando ✓"})


# ── Runner de dev ──────────────────────────────────────────────────────────
def _resolver_empresa() -> str:
    ultima = cc._ler_ultima_empresa()
    if ultima and cc._empresa_config_path(ultima).exists():
        return ultima
    existentes = cc._listar_empresas()
    if existentes:
        return existentes[0]
    # cria 'dev' mínima
    nome = "dev"
    path = cc._empresa_config_path(nome)
    path.parent.mkdir(parents=True, exist_ok=True)
    cc._escrever_config_toml({
        "fretio": {"ui_tema": "escuro"},
        "romaneio": {"cep_origem": "01001000"},
        "transportadoras": {
            "braspress": {"habilitado": True},
            "rodonaves": {"habilitado": True},
        },
    }, path)
    return nome


def _run_dev(args) -> None:
    """Modo dev: carrega o app direto numa empresa, sem partida/licença."""
    empresa = args.empresa or _resolver_empresa()
    api = Api(empresa, cc._empresa_config_path(empresa))
    window = webview.create_window(
        title=f"Fretio {api._versao} — {empresa}",
        url=_index_path(), js_api=api,
        width=1040, height=680, min_size=(900, 600), background_color="#1a1917",
    )
    api.attach_window(window)

    if args.smoke:
        def _smoke() -> None:
            for _ in range(50):
                time.sleep(0.1)
                try:
                    if window.evaluate_js("document.querySelector('#empresaName').textContent") != "—":
                        break
                except Exception:
                    pass
            try:
                result = {
                    "ok": True,
                    "empresa_render": window.evaluate_js("document.querySelector('#empresaName').textContent"),
                    "versao_render": window.evaluate_js("document.querySelector('#footerVersion').textContent"),
                    "kpis": window.evaluate_js("document.querySelectorAll('#kpiGrid .kpi').length"),
                    "carriers": window.evaluate_js("document.querySelectorAll('.carrier').length"),
                }
            except Exception as exc:
                result = {"ok": False, "erro": str(exc)}
            Path(args.smoke).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            window.destroy()
        threading.Thread(target=_smoke, daemon=True).start()
    elif args.demo:
        window.events.loaded += lambda: threading.Thread(target=_demo_pushes, args=(api,), daemon=True).start()

    webview.start()


def _run_producao() -> None:
    """Produção: partida de processo + tela de partida (licença/versão/update/empresa)."""
    from app_bootstrap import run_process_startup

    cont, _logger = run_process_startup()
    if not cont:
        return
    try:
        from usage_reporter import report_app_started
        report_app_started()
    except Exception:
        pass

    api = Api()
    window = webview.create_window(
        title="Fretio", url=_startup_path(), js_api=api,
        width=960, height=660, min_size=(840, 580), background_color="#1a1917",
    )
    api.attach_window(window)
    webview.start()


def main() -> None:
    parser = argparse.ArgumentParser(description="Fretio (UI web, pywebview/WebView2)")
    parser.add_argument("--empresa", default=None, help="[dev] abre direto nesta empresa, sem licença")
    parser.add_argument("--demo", action="store_true", help="[dev] dispara pushes de demonstração")
    parser.add_argument("--smoke", metavar="OUT", help="[dev] verificação headless e sai")
    args = parser.parse_args()

    if args.empresa or args.demo or args.smoke:
        _run_dev(args)
    else:
        _run_producao()


if __name__ == "__main__":
    main()
