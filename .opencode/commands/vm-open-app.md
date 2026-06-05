---
description: Abrir o programa real na VM Windows 10
---

Abra o programa real do Fretio na VM Windows 10 usando o runner interativo.

Resultado da VM:

!`bash scripts/opencode/vm-test.sh open-app`

Analise o log retornado.

Se falhou:
- identifique o entrypoint correto do app desktop;
- ajuste scripts/opencode/windows/app-command.txt;
- não altere servidor;
- não altere providers funcionando;
- não adicione comentários óbvios;
- rode novamente `bash scripts/opencode/vm-test.sh open-app`.

Se passou:
- informe a evidência objetiva: comando usado, processo abriu e screenshot gerado.
