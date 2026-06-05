---
description: Testar Translovato na VM Windows 10
---

Execute o teste da Translovato na VM Windows 10.

Resultado da VM:

!`bash scripts/opencode/vm-test.sh real-translovato`

Analise o log retornado.

Se falhou:
- corrija somente o fluxo desktop da Translovato;
- verifique especialmente CNPJ do destinatário, autocomplete e campos que apagam após preenchimento;
- não misture alterações do servidor;
- não altere providers funcionando;
- não adicione comentários óbvios;
- rode novamente `bash scripts/opencode/vm-test.sh real-translovato` depois da correção.

Se passou:
- informe a evidência objetiva do teste.
