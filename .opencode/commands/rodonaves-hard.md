---
description: Corrigir Rodonaves usando Codex forte.
agent: build
model: openai/gpt-5.5
---

Use as skills:
- desktop-scope
- provider-captcha
- no-comment-noise
- token-economy
- test-evidence

Escopo: somente Rodonaves no desktop.

Tarefa:
$ARGUMENTS

Obrigatório:
1. investigar antes de editar;
2. não reabrir navegador visível;
3. não reidratar campos;
4. não refazer cotação em modo visível;
5. não criar retry cego;
6. fazer a menor alteração possível;
7. testar ou justificar claramente ausência de teste.
