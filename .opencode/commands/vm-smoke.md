---
description: Testar o Fretio na VM Windows 10
---

Execute o teste smoke do Fretio na VM Windows 10.

Resultado da VM:

!`bash scripts/opencode/vm-test.sh smoke`

Analise o log retornado.

Se falhou:
- corrija somente o necessário;
- não misture desktop com servidor;
- não altere comportamento já funcional;
- não adicione comentários óbvios;
- rode novamente `bash scripts/opencode/vm-test.sh smoke` depois da correção.

Se passou:
- informe a evidência objetiva do teste.
