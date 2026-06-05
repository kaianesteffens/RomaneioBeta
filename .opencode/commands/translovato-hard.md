---
description: Corrigir Translovato usando Codex forte.
agent: build
model: openai/gpt-5.5
---

Use as skills:
- desktop-scope
- provider-captcha
- no-comment-noise
- token-economy
- test-evidence

Escopo: somente Translovato no desktop.

Tarefa:
$ARGUMENTS

Obrigatório:
1. investigar antes de editar;
2. preservar preenchimento automático existente;
3. corrigir apagamento/sobrescrita de CNPJ/destinatário com evidência;
4. não usar sleeps aleatórios como solução principal;
5. fazer a menor alteração possível;
6. testar ou justificar claramente ausência de teste.
