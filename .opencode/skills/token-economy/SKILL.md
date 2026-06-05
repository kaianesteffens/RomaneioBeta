---
name: token-economy
description: Reduz consumo de contexto e evita leitura desnecessária de arquivos.
---

Ao investigar tarefas neste projeto:

- Comece com arquivos explicitamente citados pelo usuário.
- Use grep/glob antes de abrir muitos arquivos.
- Não leia logs enormes inteiros; procure erro, traceback, provider e timestamp.
- Não abra diretórios ignorados: .venv, logs grandes, build, dist, node_modules, __pycache__.
- Não repetir instruções já presentes em AGENTS.md, PROJECT_MAP.md e TASKS.md.
- Não resumir o projeto inteiro se a tarefa for localizada.
- Antes de rodar teste demorado, explique o comando e peça aprovação.
- Em resposta final, mostrar somente:
  - causa;
  - arquivos alterados;
  - testes;
  - risco residual;
  - próximo passo.
