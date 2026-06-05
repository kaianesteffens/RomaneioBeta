---
name: provider-captcha
description: Regras para providers Playwright, CAPTCHA, Rodonaves e Translovato.
---

Ao trabalhar em providers Playwright do Fretio/RomaneioBeta:

- Não misture desktop com servidor.
- Não altere FastAPI, PostgreSQL, Alembic, Docker ou server quando a tarefa for provider desktop.
- Preserve o fluxo automático local com Playwright.
- Não implemente fallback comportamental sem autorização explícita.

Rodonaves/CAPTCHA:
- Não reabrir navegador visível.
- Não reidratar campos preenchidos.
- Não refazer cotação em modo visível.
- Não criar retry cego.
- O preenchimento automático deve ocorrer sem exigir preenchimento manual duplicado.
- Se uma exigência for tecnicamente inviável, reporte a limitação com evidência.

Translovato:
- Validar preenchimento persistente de CNPJ/destinatário.
- Evitar correção que apaga ou sobrescreve campo já preenchido corretamente.
- Não corrigir apagamento de campo com sleeps aleatórios sem evidência.

Antes de editar:
1. identificar arquivo alvo;
2. explicar causa provável;
3. definir teste mínimo.

Depois de editar:
1. mostrar diff relevante;
2. mostrar teste executado;
3. informar risco residual.
