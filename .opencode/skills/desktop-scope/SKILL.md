---
name: desktop-scope
description: Mantém tarefas do RomaneioBeta/Fretio separadas entre desktop e servidor.
---

Neste projeto existem escopos separados:

Desktop:
- PySide6;
- Playwright local;
- providers de transportadoras;
- automação de navegador;
- fluxo com CAPTCHA/interação humana;
- interface desktop.

Servidor:
- FastAPI;
- PostgreSQL;
- Alembic;
- Docker;
- API;
- migrations;
- deploy.

Quando a tarefa mencionar Rodonaves, Translovato, Alfa, cotação local, PySide6, navegador, CAPTCHA ou portal de transportadora, trate como desktop.

Não altere servidor em tarefa desktop.
Não altere desktop em tarefa servidor.
Se o escopo estiver ambíguo, pare e explique a ambiguidade antes de editar.
