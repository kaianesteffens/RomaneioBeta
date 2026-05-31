# Arquitetura do Desktop

O RomaneioBeta desktop é uma aplicação local Windows. A UI é PySide6, e todo acesso aos portais das transportadoras acontece no computador do cliente com Playwright/Chromium.

## Componentes

- `app/romaneio_app.py`: janela principal PySide6 e navegação entre módulos.
- `app/company_config.py` e `app/fretio/src/fretio/config_manager.py`: seleção de empresa e leitura de `CONFIG.toml`.
- `app/cotacao/`: validação, parsing de romaneio, telemetria, sessão e orquestração de cotação.
- `app/cotacao_transportadoras.py`: compatibilidade e orquestração das transportadoras.
- `app/fretio/src/fretio/providers/`: providers das transportadoras.
- `app/fretio/src/fretio/browser/`: localização, abertura e cleanup de Chrome/Playwright.
- `app/license.py`: validação de licença local/remota.
- `app/remote_config.py`: configuração remota segura por licença.
- `app/updater.py`: descoberta e aplicação de atualização.
- `app/error_reporter.py`, `app/usage_reporter.py`, `app/quotation_jobs_client.py`: chamadas best-effort ao servidor.

## Responsabilidade do desktop

O desktop é dono da operação sensível:

- Guardar credenciais locais das transportadoras.
- Abrir Playwright/Chromium.
- Preencher portais.
- Ler PDF/XML/DANFE localmente.
- Montar `QuoteRequest`.
- Normalizar respostas de providers em `QuoteResponse`.
- Exibir resultado na UI.

O servidor não deve receber credenciais e não substitui a automação local.

## Threads e Playwright

A thread principal do Qt deve ficar restrita a widgets e eventos. Não execute Playwright, download de update, leitura pesada de arquivo ou chamada remota longa na thread principal.

Padrão atual:

- Coroutines e providers rodam via `app/async_worker.py` (`AsyncWorkerLoop`).
- Tarefas síncronas longas rodam em worker thread.
- Callbacks de progresso postam eventos Qt de `app/ui/events.py`.
- Providers implementam `async cleanup()` para fechar page, context, browser, Playwright e processos próprios.

## Integração com servidor

Endpoints públicos usados pelo desktop:

- `POST /api/licenses/validate`: valida licença.
- `POST /api/licenses/config`: busca configuração remota segura.
- `GET /api/version/latest`: descobre última versão ativa.
- `POST /api/errors`: envia erro sanitizado.
- `POST /api/usage/events`: envia evento de uso.
- `POST /api/quotations/normalize`: normalização remota opcional.
- `POST /api/quotations/jobs`: cria job de cotação para auditoria/status.
- `GET /api/quotations/jobs/{job_id}`: consulta job da própria licença/máquina.
- `PATCH /api/quotations/jobs/{job_id}/result`: envia resultado sanitizado.

Essas chamadas são auxiliares. Falhas de rede não devem travar a cotação local, exceto quando a política de licença/update exigir bloqueio.

## O que não enviar ao servidor

Não enviar:

- Senhas, logins, cookies, tokens ou headers de sessão.
- `ADMIN_TOKEN`, `DATABASE_URL`, token GitHub ou secrets de CI.
- HTML bruto de portais, screenshots, PDFs, XMLs completos ou DANFEs completos.
- CPF/CNPJ completos de destinatários ou chaves completas de NF-e.
- Tracebacks sem sanitização.
- Arquivos `CONFIG.toml`, `license.key` ou caches locais.

Quando for necessário diagnosticar, enviar apenas contexto mínimo, status, provider, etapa, versão, erro sanitizado e metadados sem credenciais.
