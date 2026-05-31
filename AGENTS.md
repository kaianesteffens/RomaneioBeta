# AGENTS.md

Instrucoes para Codex CLI, Claude Code CLI e outros agentes trabalharem neste repositorio sem gastar contexto a toa.

## Projeto

Este repositorio contem o Fretio/RomaneioBeta Desktop, um aplicativo desktop Windows em Python 3.12 para romaneios, cotacao de frete, frete de fornecedores, rastreio e apoio operacional.

Principais tecnologias:

- UI local em PySide6.
- Automacao de portais com Playwright + Chromium no computador do cliente.
- Leitura de PDFs com pdfplumber e leitura de XML/DANFE de NF-e.
- Build Windows com PyInstaller e Inno Setup.
- Releases e assets pelo GitHub Actions.
- Integracao com `RomaneioBeta-server` para licenca, configuracao remota, versao, logs sanitizados, eventos de uso e jobs de cotacao.

## Como economizar contexto

- Leia este arquivo primeiro.
- Depois consulte `PROJECT_MAP.md` para localizar os arquivos provaveis.
- Abra apenas os arquivos necessarios para a tarefa atual.
- Nao faca varredura geral do repositorio se a tarefa for pontual.
- Nao carregue pastas de build, assets gerados, caches, instaladores, `.venv`, `dist`, `build`, `__pycache__` ou arquivos grandes sem motivo direto.
- Quando descobrir algo util sobre a estrutura do projeto, atualize somente a parte relevante de `PROJECT_MAP.md`.

## Regras de trabalho

- Entenda o projeto como app desktop Windows, nao como app web.
- Leia o codigo existente antes de alterar comportamento.
- Sempre procure o fluxo existente antes de criar um novo.
- Mantenha mudancas pequenas, diretas e alinhadas aos padroes ja usados no repositorio.
- Antes de alterar, diga quais arquivos pretende mexer e por quê.
- Ao terminar, diga quais arquivos foram alterados e como testar.
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

## Responsabilidades entre repositorios

- `RomaneioBeta`: aplicativo desktop, UI, automacoes locais, providers, updater e build Windows.
- `RomaneioBeta-server`: API FastAPI, licencas, configuracao remota, versoes, logs, eventos, jobs e painel admin.
- `RomaneioBeta-releases`: repositorio publico usado apenas para distribuicao/metadata de releases quando aplicavel.

## Verificacao

- Rode testes focados quando a tarefa tocar codigo.
- Para mudancas documentais, confirme que os links e caminhos citados existem ou marque como pendente no `PROJECT_MAP.md`.
- Para mudancas de limpeza documental ou metadados, confirme com `git status` e busca textual que nao sobraram instrucoes ativas de agentes antigos.

## Como responder ao Eduardo

Eduardo nao quer depender de terminal nem ficar cacando pastas. Explique em linguagem simples:

1. o que encontrou;
2. o que vai alterar;
3. quais arquivos mexeu;
4. como testar pela interface sempre que possivel;
5. comando de terminal somente quando for realmente necessario.