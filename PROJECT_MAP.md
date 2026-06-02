# PROJECT_MAP.md

## Objetivo

Mapa operacional do Fretio/RomaneioBeta Desktop para Codex CLI, Claude Code CLI e outros agentes. Use este arquivo para localizar partes do projeto sem abrir dezenas de arquivos a cada tarefa.

## Regras para agentes

- Leia `AGENTS.md` primeiro.
- Use este mapa para decidir quais arquivos abrir.
- Para tarefas pontuais, nao faca varredura geral do repositorio.
- Depois de mudar comportamento ou descobrir estrutura nova, atualize apenas a secao relevante deste arquivo.
- Nunca cole secrets reais, credenciais, tokens, CONFIG.toml real, PDFs/XMLs reais ou dados de cliente.

## Visao geral

Aplicativo desktop Windows para:

- Romaneio
- Cotacao de frete
- Frete de fornecedores
- Rastreio
- Licenciamento
- Configuracao remota
- Telemetria/logs sanitizados
- Atualizacao automatica

Stack principal:

- Python 3.12
- PySide6 para UI local
- Playwright + Chromium para automacao local de portais
- PyInstaller para empacotamento
- Inno Setup para instalador
- GitHub Actions/Releases para distribuicao

## Repositorios relacionados

- `kaianesteffens/RomaneioBeta`: desktop, UI, automacoes locais, providers, updater e instalador.
- `kaianesteffens/RomaneioBeta-server`: API FastAPI para licencas, configuracao remota, versoes, logs, eventos, jobs e admin.
- `kaianesteffens/RomaneioBeta-releases`: repositorio publico/auxiliar para releases e metadados quando usado pelo updater.

## Entradas principais

- `app/romaneio_app.py`: entrada da interface PySide6, janela principal, navegacao, startup, update, licenca, configuracao remota, eventos de UI e chamadas para modulos.
- `python app/romaneio_app.py`: comando de desenvolvimento citado no README.
- `app/version.txt`: versao usada em producao.
- `app/CONFIG.example.toml`: exemplo versionado de configuracao. Nao versionar `CONFIG.toml` real.

## Interface PySide6

Arquivo principal:

- `app/romaneio_app.py`

Responsabilidades observadas:

- Importa PySide6 (`QApplication`, `QMainWindow`, `QWidget`, `QTabWidget`, `QStackedWidget`, tabelas, botoes, dialogs e layouts).
- Carrega fontes e componentes de UI via `ui_components.py`.
- Usa eventos em `app/ui/events.py` para comunicacao segura com a thread da UI.
- Usa formatacao em `app/ui/formatting.py` para mascaras de CEP, CNPJ, moeda e decimal.
- Usa `app/ui/widgets.py` para widgets auxiliares.
- Controla startup, licenca, configuracao remota e update.
- Dispara cotacao, rastreio, importacao de NF-e e processamento de romaneio.

Arquivos auxiliares de UI:

- `app/ui/events.py`: eventos Qt customizados como progresso de cotacao, importacao de NF-e, rastreio e update.
- `app/ui/formatting.py`: mascaras de campos.
- `app/ui/widgets.py`: componentes visuais auxiliares.
- `app/ui_components.py`: componentes visuais, icones, fontes, nav e toggles.

Regra importante:

- Nao execute Playwright, leitura pesada de arquivo, update ou chamada remota longa na thread principal do Qt.
- Use `app/async_worker.py` e workers/threading ja existentes.

## Configuracao por empresa

Arquivos principais:

- `app/company_config.py`: cria, lista, migra, renomeia e salva configuracoes por empresa, incluindo defaults não destrutivos como `romaneio.cnpj_pagador_padrao`.
- `app/fretio/src/fretio/config_manager.py`: leitura de configuracao dentro do pacote Fretio.
- `app/CONFIG.example.toml`: exemplo seguro.

Locais citados pela documentacao:

- `%APPDATA%\Fretio\empresas\<empresa>\CONFIG.toml`
- `%APPDATA%\Fretio` para dados locais.

Cuidados:

- Nao versionar `CONFIG.toml` real.
- Nao inserir credenciais reais de transportadoras no repositorio.
- Providers devem ler apenas sua secao em `[transportadoras.<nome>]`.

## Licenca e configuracao remota

Arquivos principais:

- `app/license.py`: validacao local/remota, chave salva, machine id e status da licenca.
- `app/remote_config.py`: busca configuracao remota segura por licenca.
- `app/remote_permissions.py`: permissoes/feature flags vindas do server.
- `app/version_policy.py`: politica de versao minima e update obrigatorio.

Fluxo resumido:

1. Desktop carrega empresa e CONFIG.toml.
2. Valida licenca usando `license_api_url`.
3. Busca configuracao remota usando `license_config_api_url`.
4. Aplica permissoes: cotacao, rastreio, NF-e, romaneio, transportadoras e versao minima.
5. Usa cache/defaults quando permitido e quando servidor estiver indisponivel.

## Atualizacao

Arquivos principais:

- `app/updater.py`: descoberta e aplicacao de updates.
- `app/version.txt`: versao atual.
- `docs/UPDATE.md`: documentacao do update.
- `docs/RELEASE.md`: checklist de release.
- `installer/`: build/empacotamento.
- `.github/workflows/`: build/release automatizado.

Fluxo:

1. Consulta primeiro `version_api_url` no server.
2. Se indisponivel, pode usar GitHub Releases via `github_repo` e aliases.
3. Baixa ZIP de update.
4. Valida estrutura do ZIP.
5. Aplica update e reinicia quando necessario.

Cuidados:

- Nao remover validacao de assinatura/estrutura de update.
- Antes de mexer em update, avaliar impacto em PyInstaller, Inno Setup, ZIP, `version.txt` e workflow.

## Cotacao: fachada e modulo novo

Fachada legada:

- `app/cotacao_transportadoras.py`

Esse arquivo mantem compatibilidade com imports antigos e delega para o pacote `app/cotacao/`.

Modulo dividido:

- `app/cotacao/common.py`: tipos comuns e constantes.
- `app/cotacao/config.py`: carregamento e ajustes seguros de configuracao.
- `app/cotacao/validation.py`: validacoes.
- `app/cotacao/romaneio_parser.py`: extracao de dados do romaneio colado/processado.
- `app/cotacao/session_manager.py`: sessoes/providers reaproveitaveis.
- `app/cotacao/orchestrator.py`: prepara dados, valida e executa providers.
- `app/cotacao/telemetry.py`: eventos de uso.
- `app/cotacao/jobs_client.py`: jobs de cotacao no server.
- `app/cotacao/error_context.py`: contexto de erro por provider.

Fluxo de cotacao:

1. UI chama `cotar_transportadoras(...)` ou `cotar_transportadoras_romaneio_colado(...)` pela fachada.
2. Fachada carrega config, cria job best-effort e normalizacao shadow quando aplicavel.
3. Parser monta dados: CEP origem/destino, UF, CNPJ destinatario, peso, valor, volumes e cubagens.
4. `app/cotacao/orchestrator.py` valida dados obrigatorios.
5. `ProviderFactory` instancia providers configurados e habilitados.
6. Providers executam Playwright localmente.
7. Resultados voltam como `ResultadoCotacao`/`QuoteResponse`.
8. UI exibe resultados.
9. Uso, erros e job result sao enviados ao server em modo best-effort.

Bloqueios comuns antes de cotar:

- CEP origem invalido.
- CEP destino ausente/invalido.
- UF divergente do CEP.
- CNPJ destinatario ausente/invalido.
- Peso invalido.
- Valor negativo.
- Cubagens ausentes.
- Soma de volumes divergente das cubagens.
- Transportadora desabilitada, nao atendida ou com configuracao incompleta.

## Contrato de cotacao

Arquivo principal:

- `app/fretio/src/fretio/quotation_contract.py`

Classes/funcao importantes:

- `QuoteRequest`: entrada padronizada de cotacao.
- `QuoteResponse`: resposta padronizada.
- `QuoteStatus`: `ok`, `sem_cotacao`, `erro`, `desabilitada`, `nao_atendido`.
- `sanitize_raw_payload(...)`: remove dados sensiveis de payload bruto.
- `quote_request_from_legacy_kwargs(...)`: converte kwargs antigos para contrato novo.
- `cotacao_legada_to_quote_response(...)`: adapta resultado legado.
- `resultado_cotacao_to_quote_response(...)`: adapta `ResultadoCotacao`.
- `quote_response_to_resultado_cotacao(...)`: volta para formato usado pela UI/fachada.

Cuidados:

- `raw` e metadados nao devem conter senha, cookie, token, CPF/CNPJ completo, HTML bruto, XML/PDF completo ou screenshot.
- Tratar rota nao atendida como `nao_atendido`, portal sem valor como `sem_cotacao` e falha tecnica como `erro`.

## Providers Playwright

Pasta principal:

- `app/fretio/src/fretio/providers/`

Factory:

- `app/fretio/src/fretio/providers/factory.py`

Providers registrados:

- `braspress` -> `fretio.providers.braspress_playwright.BraspressPlaywrightProvider`
- `bauer` -> `fretio.providers.bauer_auto.BauerAutoProvider`
- `trd` -> `fretio.providers.trd.TRDProvider`
- `agex` -> `fretio.providers.agex.AGEXProvider`
- `eucatur` -> `fretio.providers.eucatur.EucaturProvider`
- `rodonaves` -> `fretio.providers.rodonaves.RodonavesProvider`
- `alfa` -> `fretio.providers.alfa.AlfaProvider`
- `coopex` -> `fretio.providers.coopex.CoopexProvider`
- `translovato` -> `fretio.providers.translovato.TranslovatoProvider`

Campos minimos por provider:

- `braspress`: `cnpj`, `senha`
- `bauer`: `cotacao_url`, `cnpj_pagador`, `cnpj_remetente`, `cnpj_destinatario`
- `trd`: `email`, `senha`
- `agex`: `email`, `senha`
- `eucatur`: login mínimo `dominio`, `usuario`, `senha`; `cnpj_pagador` é resolvido só na cotação, primeiro na transportadora e depois em `romaneio.cnpj_pagador_padrao`.
- `rodonaves`: `dominio`, `usuario`, `senha`, `cnpj_pagador`
- `alfa`: `login`, `senha`
- `coopex`: login mínimo `dominio`, `usuario`, `senha`; `cnpj_pagador` é resolvido só na cotação, primeiro na transportadora e depois em `romaneio.cnpj_pagador_padrao`.
- `translovato`: `cnpj`, `usuario`, `senha`

Para criar provider novo:

1. Criar arquivo em `app/fretio/src/fretio/providers/<nome>.py`.
2. Herdar de `ProviderBase`.
3. Implementar `async def cotar(self, request: QuoteRequest) -> QuoteResponse`.
4. Implementar `async def cleanup(self) -> None` se abrir browser/page/context.
5. Registrar em `_PROVIDER_SPECS` no `factory.py`.
6. Adicionar campos obrigatorios em `_REQUIRED_FIELDS`.
7. Atualizar `app/CONFIG.example.toml`.
8. Atualizar `docs/PROVIDERS.md` e testes.

Boas praticas:

- Nao tocar PySide6 dentro de provider.
- Nao criar event loop proprio.
- Usar seletores robustos de Playwright.
- Preservar cleanup de page/context/browser/Playwright/processos.
- Em providers que reutilizam sessao (ex.: RODONAVES), validar page/context/browser antes de `goto()` e registrar URL alvo, etapa anterior, `headless` e motivo quando houver fechamento de lifecycle.
- RODONAVES usa reCAPTCHA no portal `cliente.rte.com.br`; preferir modo visível/off-screen (`headless=false`) e, se a sessão iniciar headless e o CAPTCHA bloquear a cotação, refazer apenas a tentativa da RODONAVES em modo visível com diagnóstico seguro.
- Diagnósticos locais de provider devem guardar só metadados seguros (URL, etapa, flags de seletores, contagens, trechos redigidos), sem HTML bruto, cookies, tokens, senhas, CNPJ/CPF completo ou dados reais de cliente.
- Manter `last_error` informativo.
- Nao salvar senha/logins/cookies em log.

## Browser e Playwright

Arquivos/pastas:

- `app/fretio/src/fretio/browser/`: abertura, localizacao e cleanup de browser.
- `app/fretio/src/fretio/providers/base.py`: base dos providers e localizacao de Chrome.
- `app/cotacao/orchestrator.py`: trata Chrome ausente e reporta erro sanitizado.

Regra:

- Playwright roda no desktop do cliente, nunca no server.
- Se Chrome/Chromium estiver ausente, mostrar mensagem ao usuario e reportar erro sanitizado.

## Romaneio, NF-e e rastreio

Arquivos citados/importados pela UI:

- `app/extrator_pedidos.py`: extracao de pedidos/romaneio.
- `app/extrator_nfe.py`: leitura/importacao de NF-e/XML/DANFE.
- `app/rastreamento.py`: rastreio e links.
- `app/cotacao/romaneio_parser.py`: parser de romaneio colado.

Fluxos provaveis:

- Romaneio PDF/texto -> extrator/parser -> dados de envio -> cotacao.
- NF-e/XML/DANFE -> `extrator_nfe.py` -> dados resumidos -> UI/cotacao.
- Rastreio -> `rastreamento.py` -> resultados na UI -> evento de uso.

## Server/API usada pelo desktop

Repositorio relacionado:

- `kaianesteffens/RomaneioBeta-server`

Endpoints publicos usados pelo desktop:

- `POST /api/licenses/validate`
- `POST /api/licenses/config`
- `GET /api/version/latest`
- `POST /api/errors`
- `POST /api/usage/events`
- `POST /api/quotations/normalize`
- `POST /api/quotations/jobs`
- `GET /api/quotations/jobs/{job_id}`
- `PATCH /api/quotations/jobs/{job_id}/result`

Responsabilidade do server:

- Validar licenca.
- Entregar config remota segura.
- Publicar versao atual.
- Receber erros sanitizados.
- Receber eventos de uso.
- Registrar jobs de cotacao.
- Normalizar payloads em modo auxiliar.
- Servir painel admin.

O server nao deve:

- Rodar Playwright.
- Fazer login em transportadoras.
- Ler PDF/XML/DANFE.
- Armazenar senha de transportadora.

## Telemetria, erros e jobs

Arquivos desktop:

- `app/error_reporter.py`: erros sanitizados.
- `app/usage_reporter.py`: eventos de uso.
- `app/quotation_jobs_client.py`: cliente de jobs.
- `app/quotation_normalization_client.py`: normalizacao remota shadow/opcional.

Regra:

- Essas chamadas sao best-effort e nao devem travar cotacao local, salvo politica explicita de licenca/update.

## Build, release e instalador

Arquivos/pastas:

- `installer/`
- `installer/build.bat`
- `installer/Fretio.spec`
- `installer/launcher.py`
- `installer/launcher.spec`
- `installer/Fretio-installer.iss`
- `installer/requirements-lock.txt`
- `.github/workflows/`
- `docs/RELEASE.md`
- `docs/UPDATE.md`

Fluxo atual do workflow `Desktop CI` (`.github/workflows/ci.yml`):

- Dispara em `pull_request` contra `master` e tambem por `workflow_dispatch`, sem filtros de paths.
- Roda em Linux com Python 3.12, instala dependencias de `installer/requirements.txt`, `requirements-dev.txt` se existir, e `pytest`.
- Executa `compileall` em `app` e testes raiz, depois `pytest -q` com deselect apenas de testes antigos ja conhecidos que ainda falham fora do escopo do CI inicial.
- Nao roda build de instalador, Playwright interativo, release, assinatura de update nem exige credenciais reais.

Fluxo atual do workflow `Build and Release Fretio`:

- Execucao manual via `workflow_dispatch` com input obrigatorio `version` (`X.Y` ou `X.Y.Z`).
- `publish_release=true` e release oficial exigem `RELEASES_TOKEN`, `UPDATE_SIGNING_PRIVATE_KEY_B64` e `UPDATE_PUBLIC_KEY_B64`.
- `publish_release=false` serve apenas para artefato interno; se faltar assinatura, exige `ALLOW_UNSIGNED_DEV_RELEASE=true` e nao publica release externa.

Cuidados:

- Mudancas em dependencia podem quebrar PyInstaller.
- Mudancas em paths podem quebrar Inno Setup ou update ZIP.
- Antes de alterar build, verificar impacto no launcher, assets, assinatura e versao.

## Documentacao existente

- `README.md`: visao geral, stack, fluxo principal e desenvolvimento.
- `docs/ARCHITECTURE.md`: arquitetura local, threads, Playwright e integracao com server.
- `docs/PROVIDERS.md`: contrato e criacao de providers.
- `docs/LICENSING.md`: licenca, config remota e dados permitidos.
- `docs/UPDATE.md`: descoberta de versao e update.
- `docs/RELEASE.md`: checklist de release.
- `CHANGELOG.md`: historico de mudancas.

## Onde mexer por tipo de tarefa

- Erro visual/tela/botao: comece por `app/romaneio_app.py`, depois `app/ui/*` e `app/ui_components.py`.
- Erro de cotacao geral: comece por `app/cotacao_transportadoras.py`, `app/cotacao/orchestrator.py` e `app/cotacao/romaneio_parser.py`.
- Erro em transportadora especifica: comece por `app/fretio/src/fretio/providers/<transportadora>.py` e `factory.py`.
- Erro de credenciais/config: comece por `app/company_config.py`, `app/CONFIG.example.toml`, `factory.py` e validacao do provider.
- Erro de licenca: comece por `app/license.py`, `app/remote_config.py`, `app/remote_permissions.py` e server `app/routers/licenses.py`.
- Erro de update: comece por `app/updater.py`, `app/version.txt`, `docs/UPDATE.md` e `installer/`.
- Erro de logs/telemetria/jobs: comece por `app/error_reporter.py`, `app/usage_reporter.py`, `app/quotation_jobs_client.py` e server correspondente.
- Erro de build: comece por `installer/`, `.github/workflows/`, `requirements` e specs.

## Checklist antes de pedir ao agente para programar

Use um pedido assim:

```txt
Leia AGENTS.md e PROJECT_MAP.md.
A tarefa e: <descrever problema>.
Abra somente os arquivos provaveis para essa tarefa.
Antes de alterar, diga quais arquivos pretende mexer.
Faca a menor mudanca possivel.
Atualize PROJECT_MAP.md se descobrir estrutura nova.
Explique como testar pela interface.
```

## Pendencias de mapeamento futuro

- Detalhar classes/metodos principais dentro de `app/romaneio_app.py`.
- Mapear todos os arquivos em `app/fretio/src/fretio/browser/`.
- Mapear cada provider individualmente: login, cotacao, seletores, cleanup e erros comuns.
- Mapear fluxo completo de rastreio.
- Mapear fluxo completo de NF-e/XML/DANFE.
- Mapear workflows de GitHub Actions e assets de release.
