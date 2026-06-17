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
from web_presenters import (  # noqa: E402
    chave_nota,
    montar_romaneio_fornecedor,
    nota_card,
    validar_local_entrega,
)

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


# Apresentação (nota_card / validar_local_entrega / montar_romaneio_fornecedor)
# foi movida para web_presenters.py e importada acima.


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

    @staticmethod
    def _norm_ufs(value) -> list:
        """Normaliza ufs_atendidas: aceita lista OU string separada por vírgula
        (configs antigas guardam "SP,RS"). Retorna UFs em maiúsculas, sem vazios."""
        items = value.split(",") if isinstance(value, str) else (value or [])
        return [str(x).strip().upper() for x in items if str(x).strip()]

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
                "ufs_atendidas": self._norm_ufs(sec.get("ufs_atendidas")),
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
                t["ufs_atendidas"] = self._norm_ufs(data.get("ufs_atendidas"))

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
            # restart_app() lança o _apply_update.bat e chama sys.exit(0); mas este
            # método roda numa thread do bridge do pywebview, onde sys.exit encerra só
            # a thread, não o processo. O .bat espera ESTE PID terminar para copiar os
            # arquivos — sem encerrar o processo, o update trava em "Reiniciando…".
            try:
                restart_app()  # lança o .bat
            except SystemExit:
                pass           # o sys.exit(0) interno não mata o processo aqui
            except Exception:
                pass
            try:
                self._teardown()  # fecha Chrome/Playwright antes de sair
            except Exception:
                pass
            os._exit(0)  # encerra o PROCESSO inteiro -> o .bat aplica e reinicia
        return {"ok": False, "erro": "Não foi possível aplicar a atualização agora."}

    def startup_empresas(self) -> list:
        # Semeia a config por empresa a partir de um CONFIG.toml legado (raiz) em
        # upgrades, ANTES de listar — senão a lista vem vazia e o usuário perderia
        # as credenciais existentes. Idempotente (no-op se já houver empresas).
        try:
            cc._migrar_config_se_necessario()
        except Exception:
            pass
        return cc._listar_empresas()

    def startup_criar_empresa(self, nome: str) -> dict:
        import re
        # Sanitiza separadores/reservados (espelha o seletor Qt antigo) antes de
        # criar a pasta, e rejeita nomes que escapem de empresas/ (path traversal).
        nome = re.sub(r'[<>:"/\\|?*]', "_", str(nome or "").strip())
        if not nome or nome in (".", ".."):
            return {"ok": False, "erro": "Informe um nome válido para a empresa."}
        try:
            dest = cc._empresa_config_path(nome).resolve()
            if cc._empresas_dir().resolve() not in dest.parents:
                return {"ok": False, "erro": "Nome de empresa inválido."}
        except Exception:
            return {"ok": False, "erro": "Nome de empresa inválido."}
        # Duplicata case-insensitive (o filesystem do Windows não diferencia caixa).
        if nome.lower() in {e.lower() for e in cc._listar_empresas()}:
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
        # Trocar de empresa reusa a mesma instância Api (navegação por load_url),
        # então encerra a sessão/loop da empresa anterior e zera o estado em memória
        # — senão a cotação/rastreio da empresa B reusaria a sessão/credenciais da A
        # (e o dashboard/NF-e vazaria entre empresas).
        try:
            self._teardown()
        except Exception:
            pass
        self._notas = []
        self._romaneios = []
        self._last_cotacao = []
        self._romaneio_texto = ""
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


class RastreioMixin:
    """Rastreio de NF-e carregadas. Delegate de domínio do Api (opera sobre self;
    superfície pública pywebview inalterada)."""

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
        with self._op_lock:
            if self._rastreando:
                return {"erro": "Já existe um rastreamento em andamento"}
            self._rastreando = True
        try:
            self._ensure_backend()
        except Exception as exc:
            self._rastreando = False
            return {"erro": f"Falha ao preparar o rastreamento: {exc}"}
        try:
            from fretio.providers.base import find_chrome
            find_chrome()
        except FileNotFoundError:
            from cotacao_transportadoras import CHROME_MISSING_USER_MESSAGE
            self._rastreando = False
            self._emit("chrome_missing", {"texto": CHROME_MISSING_USER_MESSAGE})
            return {"erro": CHROME_MISSING_USER_MESSAGE}

        alvo = self._notas
        if chaves:
            cset = set(chaves)
            # Casa pela chave canônica (chave_nota), não por chave_acesso cru: do
            # contrário NF-e sem chave de 44 dígitos (cuja chave do frontend é
            # "nf-<numero>") nunca entrariam no subconjunto selecionado.
            sub = [n for n in self._notas if chave_nota(n) in cset]
            if sub:
                alvo = sub
        fut = self._loop.submit(lambda: self._coro_rastreio(list(alvo)))
        if fut is None:
            self._rastreando = False
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
            chaves.append(chave_nota(nf))

        def _cb(indice: int, total: int, resultado: Any) -> None:
            chave = chaves[indice - 1] if 0 <= indice - 1 < len(chaves) else ""
            self._emit("rastreio_progress", {
                "chave": chave, "indice": indice, "total": total,
                "resultado": self._ser_rastreio(resultado),
            })

        try:
            from usage_reporter import report_tracking_started, report_tracking_finished
        except Exception:
            def report_tracking_started(*a, **k):
                pass

            def report_tracking_finished(*a, **k):
                pass

        try:
            report_tracking_started(metadata={"total": len(notas_track)})
        except Exception:
            pass

        try:
            resultados = await rastrear_multiplas(notas_track, callback=_cb)
            entregues = sum(1 for r in resultados if getattr(r, "entregue", False))
            com_ss = sum(1 for r in resultados if getattr(r, "screenshot_path", ""))
            self._emit("rastreio_finished", {
                "total": len(resultados), "entregues": entregues, "screenshots": com_ss,
            })
            try:
                report_tracking_finished("ok", metadata={"total": len(resultados), "entregues": entregues})
            except Exception:
                pass
        except Exception as exc:
            self._emit("rastreio_finished", {
                "total": 0, "entregues": 0, "screenshots": 0, "erro": str(exc),
            })
            try:
                report_tracking_finished("error", metadata={"erro": type(exc).__name__})
            except Exception:
                pass
        finally:
            self._rastreando = False


class CotacaoMixin:
    """Cotação de frete: serialização de resultado, callbacks de progresso e a
    coroutine de cotação. Delegate de domínio do Api (opera sobre self; superfície
    pública pywebview inalterada). _ensure_backend fica no Api (infra compartilhada
    com o rastreio)."""

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
        # Romaneio colado (sem cnpj_remetente) também passa pelo gate "romaneio";
        # cotação de fornecedor/FOB (cnpj_remetente preenchido) fica só sob "cotacao".
        if not str(cnpj_remetente or "").strip():
            bloqueio_rom = self._gate("romaneio")
            if bloqueio_rom:
                return bloqueio_rom
        with self._op_lock:
            if self._cotando:
                return {"erro": "Já existe uma cotação em andamento"}
            self._cotando = True
        try:
            self._ensure_backend()
        except Exception as exc:  # backend indisponível
            self._cotando = False
            return {"erro": f"Falha ao preparar a cotação: {exc}"}

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


class RomaneioMixin:
    """Romaneio por PDF e cotação de fornecedor (frete FOB). Delegate de domínio
    do Api (opera sobre self; superfície pública pywebview inalterada)."""

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

        ok, msg = validar_local_entrega(extrator, pedidos)
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
        try:
            from usage_reporter import report_romaneio_processed
            report_romaneio_processed("ok", metadata={"pedidos": len(pedidos)})
        except Exception:
            pass
        return {
            "ok": True, "texto": texto, "arquivo": Path(arq).name,
            "pedidos": len(pedidos), "destino": destino,
        }

    def get_romaneio_texto(self) -> dict:
        return {"texto": self._romaneio_texto}

    # ── Fornecedores (frete FOB) ────────────────────────────────────────────
    def _montar_romaneio_fornecedor(self, form: dict) -> tuple[str, str]:
        """Carrega a config da empresa e delega ao formatter de domínio
        (web_presenters.montar_romaneio_fornecedor)."""
        return montar_romaneio_fornecedor(_load_config(self._config_path), form)

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


class Api(ConfigMixin, StartupMixin, RastreioMixin, CotacaoMixin, RomaneioMixin):
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
        self._rastreando = False
        # Serializa o check-and-set de _cotando/_rastreando: chamadas do js_api
        # podem chegar concorrentes, então reservar a flag precisa ser atômico.
        self._op_lock = threading.Lock()
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

    # ── NF-e ────────────────────────────────────────────────────────────────
    def nfe_selecionar(self) -> dict:
        if self._window is None:
            return {"erro": "Janela indisponível"}
        bloqueio = self._gate("nfe")
        if bloqueio:
            return bloqueio
        paths = self._window.create_file_dialog(
            webview.OPEN_DIALOG,
            allow_multiple=True,
            file_types=("Arquivos XML de NFe (*.xml)", "Todos os arquivos (*.*)"),
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
        cards = [nota_card(base + i + 1, nf) for i, nf in enumerate(novas)]
        if novas:
            try:
                from usage_reporter import report_nfe_imported
                report_nfe_imported("ok", metadata={"quantidade": len(novas)})
            except Exception:
                pass
        return {"cards": cards, "erros": erros, "total_notas": len(self._notas)}

    def nfe_cards(self) -> dict:
        """Reconstrói os cards das NF-e já carregadas (self._notas é a fonte da
        verdade). A tela Rastreio chama isto no render para reexibir as notas após
        navegar para outra página e voltar — sem isto os cards se perdiam e o
        re-import era barrado pelo filtro de duplicados de nfe_selecionar."""
        cards = [nota_card(i + 1, nf) for i, nf in enumerate(self._notas)]
        return {"cards": cards, "total_notas": len(self._notas)}

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

    def abrir_app(self) -> dict:
        # A navegação é feita no JS, DEPOIS de aguardar este retorno (ver
        # app/web/startup.js). Navegar aqui via load_url trocaria a página antes
        # do pywebview resolver o callback de retorno deste método, gerando
        # JavascriptException ('_returnValuesCallbacks.abrir_app.<id> is not a
        # function'). Devolvemos o destino relativo (mesma pasta web/) e o cliente
        # navega na sequência, já com o callback resolvido — sem corrida.
        return {"ok": True, "navegar": "index.html"}

    def trocar_empresa(self) -> dict:
        """Volta para o seletor de empresa. Navegação feita no JS — ver abrir_app."""
        return {"ok": True, "navegar": "startup.html?fase=empresa"}

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


def _webview2_runtime_present() -> bool:
    """True se o runtime do Edge WebView2 está instalado (mesma checagem do
    instalador Inno: EdgeUpdate Clients\\{F3017226-...} 'pv' em HKLM/HKCU).
    Fail-open: qualquer erro assume PRESENTE, para nunca bloquear o boot por engano."""
    if os.name != "nt":
        return True
    try:
        import winreg
        client = r"SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
        candidatos = (
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"),
            (winreg.HKEY_LOCAL_MACHINE, client),
            (winreg.HKEY_CURRENT_USER, client),
        )
        for hive, sub in candidatos:
            try:
                with winreg.OpenKey(hive, sub) as key:
                    pv, _ = winreg.QueryValueEx(key, "pv")
                    if pv:
                        return True
            except OSError:
                continue
        return False
    except Exception:
        return True  # fail-open: nunca bloquear o boot por erro na checagem


def _aviso_webview2_ausente() -> None:
    msg = (
        "O Fretio precisa do componente Microsoft Edge WebView2 Runtime, que não "
        "está instalado neste computador.\n\nInstale o WebView2 Runtime (Evergreen) "
        "e abra o Fretio novamente:\nhttps://go.microsoft.com/fwlink/p/?LinkId=2124703"
    )
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, msg, "Fretio — WebView2 necessário", 0x10)
    except Exception:
        print(msg)


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

    # Cliente que atualizou in-app (Qt->WebView2) sem o runtime WebView2: abrir a
    # janela daria tela em branco. Avisa com instruções e não inicia, em vez de
    # falhar mudo. Só em build empacotado (frozen); em dev nunca bloqueia.
    if getattr(sys, "frozen", False) and not _webview2_runtime_present():
        _aviso_webview2_ausente()
        return

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
