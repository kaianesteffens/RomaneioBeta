# AGENTS.md

Instrucoes para Codex trabalhar neste repositorio.

## Projeto

Este repositorio contem o Fretio/RomaneioBeta, um aplicativo desktop Windows em Python 3.12 para romaneios, cotacao de frete, frete de fornecedores, rastreio e apoio operacional.

Principais tecnologias:

- UI em PySide6.
- Automacao de portais com Playwright + Chromium.
- Leitura de PDFs com pdfplumber.
- Leitura de XML/DANFE de NF-e.
- Build com PyInstaller e Inno Setup.
- Releases pelo GitHub Actions.

## Regras de trabalho

- Use Codex como agente principal deste projeto.
- Entenda o projeto como app desktop Windows, nao como app web.
- Leia o codigo existente antes de alterar comportamento.
- Sempre procure o fluxo existente antes de criar um novo.
- Mantenha as mudancas pequenas, diretas e alinhadas aos padroes ja usados no repositorio.
- Nao altere `app/`, `installer/`, `requirements.txt`, workflows ou arquivos de build sem necessidade direta da tarefa.
- Nao remova nem exponha secrets, tokens, senhas, chaves privadas, licencas ou credenciais. Se encontrar algo sensivel versionado, avise antes de mexer.
- Nao versionar `CONFIG.toml` real, credenciais, tokens, licencas, XMLs/PDFs reais de clientes, prints sensiveis ou dados operacionais privados.
- Preserve `.github/workflows/`, pois os workflows fazem parte do build e release.
- Para providers de transportadoras, siga a arquitetura existente em `app/fretio/src/fretio/providers/`.
- Respeite configuracao por transportadora em `CONFIG.toml` ou no exemplo versionado, sem inserir valores reais.
- Prefira seletores robustos em automacoes Playwright: `name`, `placeholder`, `role`, texto visivel e labels. Evite classes geradas e `nth-child` quando houver alternativa.
- Mantenha `last_error` informativo em fluxos que podem falhar e preserve cleanup de recursos externos como browser, context e page.
- Antes de mexer em build/release, avaliar impacto em PyInstaller, Inno Setup, `app/version.txt`, updater, ZIP de update e `.github/workflows/build-release.yml`.
- Nao remover validacao de assinatura de update nem mudar repositorio de releases sem autorizacao explicita.
- Ao mover instrucoes antigas de agentes, use `backup-agent-instructions/` em vez de apagar diretamente quando houver conteudo reutilizavel.

## Verificacao

- Rode testes focados quando a tarefa tocar codigo.
- Para mudancas de limpeza documental ou metadados, confirme com `git status` e busca textual que nao sobraram instrucoes ativas de agentes antigos.
