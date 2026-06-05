---
description: Revisar alterações antes de merge com checklist rigoroso.
agent: caveman-plan
model: openai/gpt-5.4-mini
---

Use as skills:
- pr-merge-gate
- desktop-scope
- token-economy

Analise:
!`git status`
!`git diff --stat`
!`git diff`

Não edite arquivos.
Não faça commit.
Não faça push.

Responda:
1. seguro para merge: sim/não;
2. risco de regressão;
3. arquivos fora do escopo;
4. testes mínimos;
5. recomendação final.
