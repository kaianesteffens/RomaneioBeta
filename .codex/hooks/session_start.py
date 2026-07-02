#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}

    cwd = Path(payload.get("cwd") or ".").resolve()

    context = f"""
CONTEXTO FIXO DO PROJETO ROMANEIOBETA / FRETIO

Antes de alterar qualquer arquivo, leia e respeite:
- AGENTS.md
- PROJECT_MAP.md
- TASKS.md

Não recrie AGENTS.md, PROJECT_MAP.md ou TASKS.md. Esses arquivos já existem e são a fonte principal de orientação do projeto.

Este repositório é o DESKTOP do Fretio/RomaneioBeta:
- UI web (pywebview/WebView2, app/web/*)
- Playwright local
- automações de cotação
- interface desktop
- execução principal em Windows para cliente
- desenvolvimento/testes também podem ocorrer no Linux/Zorin

Regra de escopo:
- Se a tarefa envolver Rodonaves, Translovato, Eucatur, Coopex, provider, cotação, UI desktop web (WebView2) ou Playwright local, trate como tarefa do desktop.
- Não altere servidor, FastAPI, PostgreSQL, Alembic, Docker ou deploy salvo se o usuário pedir explicitamente.
- Não misture alterações de desktop e servidor na mesma tarefa.

Regra de evidência:
- Para correção de cotação/provider, não conclua dizendo apenas que corrigiu.
- Informe arquivos alterados, teste executado e resultado.
- Se não conseguir reproduzir o erro real, diga isso claramente.

Regra de UI:
- Não invente redesign genérico.
- Se a tarefa for visual, siga a imagem/referência fornecida e preserve funcionalidade existente.
- Não faça “melhoria visual” subjetiva sem evidência ou comparação.

Diretório atual da sessão:
{cwd}
""".strip()

    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": context
        }
    }))


if __name__ == "__main__":
    main()
