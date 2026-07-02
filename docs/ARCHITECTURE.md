# Arquitetura do Desktop

O RomaneioBeta desktop é uma aplicação local Windows standalone (sem servidor). A UI é web renderizada em WebView2 via pywebview (HTML/CSS/JS locais em `app/web/*`), e todo acesso aos portais das transportadoras acontece no computador do cliente com Playwright/Chromium.

## Componentes

- `app/web_app.py`: bridge pywebview/WebView2 (classe `Api`), cria a janela e expõe os métodos consumidos pelo front.
- `app/web/`: front local (`index.html`, `app.js`, `app.css`, `format.js`, `pages/*.js`) com as telas e a navegação.
- `app/web_presenters.py`: monta os dados apresentados na UI a partir dos módulos locais.
- `app/company_config.py` e `app/fretio/src/fretio/config_manager.py`: seleção de empresa e leitura de `CONFIG.toml`.
- `app/cotacao/`: validação, parsing de romaneio, sessão e orquestração de cotação.
- `app/cotacao_transportadoras.py`: compatibilidade e orquestração das transportadoras.
- `app/fretio/src/fretio/providers/`: providers das transportadoras.
- `app/fretio/src/fretio/browser/`: localização, abertura e cleanup de Chrome/Playwright.
- `app/updater.py`: descoberta (via GitHub Releases) e aplicação de atualização.

## Responsabilidade do desktop

O desktop é dono da operação sensível:

- Guardar credenciais locais das transportadoras.
- Abrir Playwright/Chromium.
- Preencher portais.
- Ler PDF/XML/DANFE localmente.
- Montar `QuoteRequest`.
- Normalizar respostas de providers em `QuoteResponse`.
- Exibir resultado na UI.

Toda a operação é local: não há licenciamento, configuração remota nem servidor.

## Threads e Playwright

A thread da UI/bridge (pywebview/WebView2) deve ficar restrita a apresentar dados e responder chamadas do front. Não execute Playwright, download de update, leitura pesada de arquivo ou chamada remota longa bloqueando essa thread.

Padrão atual:

- Coroutines e providers rodam via `app/async_worker.py` (`AsyncWorkerLoop`).
- Tarefas síncronas longas rodam em worker thread.
- Callbacks de progresso voltam ao front pela bridge `Api` em `app/web_app.py`.
- Providers implementam `async cleanup()` para fechar page, context, browser, Playwright e processos próprios.

## Atualização

A descoberta de versão é feita via GitHub Releases (o updater consulta a release mais recente do `github_repo`). Não há servidor de versão. Detalhes em [UPDATE.md](UPDATE.md).

## Dados sensíveis em log

Como toda a operação é local, nada é enviado a servidor. Em logs locais, nunca gravar:

- Senhas, logins, cookies, tokens ou headers de sessão.
- HTML bruto de portais, screenshots, PDFs, XMLs completos ou DANFEs completos.
- CPF/CNPJ completos de destinatários ou chaves completas de NF-e.
- Tracebacks com dados reais de cliente sem sanitização.

Registrar apenas contexto mínimo: status, provider, etapa, versão e erro sanitizado, sem credenciais.
