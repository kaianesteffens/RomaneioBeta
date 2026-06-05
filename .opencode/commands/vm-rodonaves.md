---
description: Testar Rodonaves na VM Windows 10
---

Execute o teste da Rodonaves na VM Windows 10.

Resultado da VM:

!`bash scripts/opencode/vm-test.sh real-rodonaves`

Analise o log retornado.

Se falhou:
- corrija somente o fluxo desktop da Rodonaves;
- preserve a regra atual do projeto: sem fallback de reabrir navegador visível e reidratar campos;
- navegador só deve aparecer na hora exata de CAPTCHA/interação humana;
- não misture alterações do servidor;
- não altere providers funcionando;
- não adicione comentários óbvios;
- rode novamente `bash scripts/opencode/vm-test.sh real-rodonaves` depois da correção.

Se passou:
- informe a evidência objetiva do teste.
