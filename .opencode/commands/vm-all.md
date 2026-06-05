---
description: Rodar todos os testes na VM Windows 10
---

Execute todos os testes do Fretio na VM Windows 10.

Resultado da VM:

!`bash scripts/opencode/vm-test.sh all`

Analise o log retornado.

Se falhou:
- corrija somente o necessário;
- não misture desktop com servidor;
- não altere providers funcionando sem necessidade;
- não adicione comentários óbvios;
- preserve as regras existentes de Rodonaves/CAPTCHA;
- rode novamente `bash scripts/opencode/vm-test.sh all` depois da correção.

Se passou:
- informe a evidência objetiva do teste.
