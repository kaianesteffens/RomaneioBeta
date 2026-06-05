---
name: pr-merge-gate
description: Checklist para revisar diff antes de merge.
---

Ao revisar PR ou diff:

Verifique:
- se mexeu fora do escopo;
- se alterou server quando a tarefa era desktop;
- se criou fallback proibido;
- se adicionou comentários óbvios;
- se removeu comportamento que já funcionava;
- se os testes cobrem o bug;
- se há risco para Rodonaves, Translovato, login, CAPTCHA ou preenchimento automático.

Responder com:
1. seguro para merge: sim/não;
2. riscos reais;
3. arquivos suspeitos;
4. testes mínimos obrigatórios;
5. recomendação objetiva.
