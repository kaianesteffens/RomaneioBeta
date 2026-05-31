# PROJECT_MAP.md

## Objetivo

Mapa resumido do projeto para agentes. Use este arquivo para localizar componentes antes de abrir dezenas de arquivos.

## Visao geral

Aplicativo desktop Windows para:

- Romaneio
- Cotacao de frete
- Frete de fornecedores
- Rastreio
- Licenciamento
- Atualizacao automatica

Stack principal:

- Python 3.12
- PySide6
- Playwright + Chromium
- PyInstaller
- Inno Setup

## Estrutura conhecida

### Interface principal

- `app/romaneio_app.py` → janela principal e navegacao.

### Modulo de cotacao

- `app/cotacao/` → parsing, validacao, sessao, telemetria e orquestracao.
- `app/cotacao/romaneio_parser.py` → parser de romaneios.
- `app/cotacao_transportadoras.py` → orquestracao das transportadoras.

### Providers

- `app/fretio/src/fretio/providers/` → integracoes com transportadoras.

### Browser e Playwright

- `app/fretio/src/fretio/browser/` → gerenciamento de Chromium/Playwright.

### Licenca e configuracao

- `app/license.py` → validacao de licenca.
- `app/remote_config.py` → configuracao remota segura.
- `app/company_config.py` → configuracao por empresa.

### Telemetria e erros

- `app/error_reporter.py`
- `app/usage_reporter.py`
- `app/quotation_jobs_client.py`

### Atualizacao

- `app/updater.py`
- `app/version.txt`

### Build e instalador

- `installer/`

## Integracao com RomaneioBeta-server

Endpoints usados:

- licenca
- configuracao remota
- versao
- logs sanitizados
- eventos de uso
- jobs de cotacao

O desktop executa Playwright localmente.
O servidor nao executa Playwright.

## Fluxo de cotacao

1. Usuario abre o desktop.
2. Empresa e configuracao sao carregadas.
3. Licenca e configuracao remota sao validadas.
4. Romaneio e documentos sao processados localmente.
5. Providers executam automacao local via Playwright.
6. Resultado aparece na UI.
7. Eventos e logs sanitizados podem ser enviados ao servidor.

## Antes de alterar qualquer coisa

Pergunte:

- Isso e desktop ou server?
- E UI, provider, licenca, updater ou build?
- Existe implementacao semelhante no projeto?

## Pendencias

- Expandir mapa dos providers.
- Mapear telas PySide6.
- Mapear estrutura completa de configuracao por empresa.
- Mapear fluxo detalhado de cotacao e rastreio.