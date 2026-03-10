# Romaneio Beta — Instruções para o Copilot

## 🤖 Papel do Copilot neste projeto

**O Copilot é o responsável pela elaboração de todo e qualquer código deste projeto.**

O dono do projeto (`kaianesteffens`) atua como **product owner**: ele define o que precisa ser feito, aponta melhorias, relata problemas e dá instruções de mudança. O Copilot é quem **implementa tudo** — escreve, corrige, refatora e mantém o código.

### Isso significa que o Copilot deve:
- ✅ Escrever o código completo quando uma nova funcionalidade for pedida
- ✅ Implementar a solução inteira, não apenas sugerir trechos
- ✅ Tomar decisões técnicas de implementação com base no contexto do projeto
- ✅ Garantir que o novo código segue todas as convenções já estabelecidas
- ✅ Atualizar este arquivo de instruções quando o projeto mudar
- ✅ Commitar o código diretamente no repositório quando solicitado

### O dono do projeto NÃO vai:
- ❌ Escrever código manualmente
- ❌ Decidir detalhes de implementação
- ❌ Revisar sintaxe ou estrutura interna — apenas o comportamento final

---

## O que é este projeto

**Romaneio Beta** (internamente chamado de **FreteBot**) é uma aplicação desktop Windows que:
1. **Extrai pedidos de PDFs** de romaneio (ordem de compra/venda)
2. **Cota automaticamente o frete** em múltiplas transportadoras em paralelo via automação de browser (Playwright)
3. **Exibe os resultados** em uma interface gráfica PySide6
4. **Atualiza automaticamente** via GitHub Releases, sem precisar reinstalar
5. **Gerencia licenças** por máquina via chave remota em GitHub Gist

A distribuição é feita como `.exe` empacotado com PyInstaller + Inno Setup. O sistema de atualização baixa um ZIP de release do GitHub e substitui os arquivos sem reinstalar.

---

## Stack e Tecnologias

- **Linguagem:** Python 3.12
- **Interface gráfica:** PySide6 (Qt6)
- **Automação de browser:** Playwright (Chromium) — assíncrono (`asyncio`)
- **Extração de PDF:** pdfplumber
- **HTTP:** httpx, urllib (nativo)
- **HTML parsing:** BeautifulSoup4 (bs4)
- **Configuração:** TOML (`toml` / `tomli`)
- **Cache:** SQLite3 (nativo)
- **Empacotamento:** PyInstaller (one-folder mode) + Inno Setup 6
- **Versão atual:** lida de `app/version.txt` (ex: `1.48`)

---

## Estrutura de Pastas

```
RomaneioBeta/
├── app/                          → Código-fonte principal do aplicativo
│   ├── romaneio_app.py           → ENTRY POINT: Interface PySide6, toda a UI, eventos e orquestração
│   ├── cotacao_transportadoras.py→ Orquestrador das cotações paralelas (asyncio + threads)
│   ├── extrator_pedidos.py       → Extração de pedidos de PDFs (pdfplumber + regex)
│   ├── updater.py                → Auto-updater via GitHub Releases
│   ├── license.py                → Sistema de licenciamento por máquina (GitHub Gist)
│   ├── error_reporter.py         → Envio automático de erros para GitHub Gist (silencioso)
│   ├── version.txt               → Versão atual do app (ex: "1.48")
│   ├── romaneio_exemplo.csv      → CSV de exemplo para cotação manual
│   ├── dev.bat                   → Roda o app direto pelo Python (sem build)
│   ├── assets/                   → Ícones (.ico) e logos das transportadoras
│   └── fretebot/
│       └── src/
│           └── fretebot/         → Pacote Python interno do FreteBot
│               ├── __init__.py
│               ├── models.py     → Dataclasses: Pedido, Cotacao, Pesos, Relatorio
│               ├── calc.py       → Cálculo de cubagem e peso taxado
│               ├── cache.py      → Cache de cotações em SQLite (TTL: 24h)
│               ├── config.py     → Leitura do CONFIG.toml
│               ├── logging_conf.py → Configuração de logging (arquivo em %APPDATA%\FreteBot\)
│               └── providers/    → Um arquivo por transportadora
│                   ├── base.py              → Classe abstrata ProviderBase + launch_browser_resilient()
│                   ├── braspress_playwright.py
│                   ├── bauer_auto.py
│                   ├── trd.py
│                   ├── agex.py
│                   ├── eucatur.py
│                   ├── rodonaves.py
│                   ├── alfa.py
│                   ├── coopex.py
│                   ├── _win_taskbar.py      → Ocultar janelas do Chrome da barra de tarefas (Windows API)
│                   └── ...
│
└── installer/                    → Build e empacotamento
    ├── build.bat                 → Build completo (baixa Python embutido, instala deps, PyInstaller, Inno Setup)
    ├── build_update_zip.bat      → Gera ZIP de atualização para publicar no GitHub Releases
    ├── FreteBot.spec             → Configuração PyInstaller (one-folder, sem console, GUI)
    ├── FreteBot-installer.iss    → Script Inno Setup (gera Romaneio-Beta-Setup.exe)
    ├── instalar_navegador.bat    → Instala Chromium Playwright pós-instalação
    ├── requirements.txt          → Dependências Python (pdfplumber, PySide6, playwright, etc.)
    └── BUILD.md                  → Documentação do processo de build
```

---

## Arquitetura e Fluxo de Dados

### Fluxo principal (cotação via romaneio PDF)
```
Usuário abre PDF no romaneio_app.py
  → ExtratorPedidos.extrair_arquivo(pdf)   [extrator_pedidos.py]
    → pdfplumber extrai texto página por página
    → regex extrai: número pedido, CNPJ, CEP destino, valor, itens
    → retorna List[Pedido]
  → cotar_transportadoras_romaneio_colado() [cotacao_transportadoras.py]
    → cria Tasks asyncio para cada transportadora em paralelo
    → cada TransportadoraSession.cotar() chama provider.coteir()
    → providers usam Playwright/HTTP para acessar os sites
    → ResultadoCotacao coletado via asyncio.gather()
  → formatar_resultados_cotacao()
    → formata texto para exibição na UI
  → romaneio_app.py atualiza QPlainTextEdit via eventos Qt (thread-safe)
```

### Fluxo de atualização automática
```
romaneio_app.py (startup)
  → get_repo_from_config()              [updater.py] lê CONFIG.toml
  → check_for_update(repo, versao_atual)
    → GET /repos/{owner}/{repo}/releases/latest
    → compara versões numericamente
  → se há update: apply_update(info)
    → baixa ZIP do asset da release
    → extrai sobre a pasta atual
  → needs_restart() → restart_app()
```

### Sistema de Licença
```
startup → get_saved_license()     [license.py] lê %APPDATA%\FreteBot\license.key
  → validate_license(chave, machine_id)
    → GET GitHub Gist secreto (JSON com licenças válidas)
    → verifica se chave existe e não está revogada
    → salva cache de validação (.license_cache) para funcionar offline até 7 dias
  → LicenseStatus: VALID | INVALID | EXPIRED | OFFLINE_GRACE
```

---

## Modelos de Dados Principais (`fretebot/models.py`)

```python
@dataclass
class Pedido:       # Input para cotação
    origem: str     # CEP origem (8 dígitos)
    destino: str    # CEP destino (8 dígitos)
    peso: float     # kg
    valor: float    # R$ valor da nota
    altura: float   # cm
    largura: float  # cm
    profundidade: float  # cm

@dataclass
class Cotacao:      # Resultado de uma transportadora
    transportadora: str
    prazo_dias: int
    valor_frete: float
    restricoes: Optional[str]

@dataclass
class Pesos:        # Cálculo de peso
    peso_real: float
    cubagem_m3: float
    peso_cubado: float  # cubagem_m3 * fator (padrão 6000)
    peso_taxado: float  # max(peso_real, peso_cubado)
```

---

## Comunicação Thread-Safe (PySide6 + asyncio)

A UI roda na **thread principal Qt**. As cotações rodam em **thread separada** com `asyncio.run()`. A comunicação é feita via **QEvent customizados** (nunca chamar widgets diretamente de outra thread):

- `UdpateResultEvent` — atualiza texto de resultado
- `UpdateFinishedEvent` — sinaliza fim das cotações
- `StatusUpdateEvent` — atualiza barra de status
- `CotacaoProgressEvent` — progresso em tempo real por transportadora
- `LoginStatusEvent` — status de login (`"pending" | "ok" | "fail"`)
- `LoginRetryPromptEvent` — pede ao usuário para refazer login manual

**Regra:** Nunca chamar `widget.setText()` ou similar de outra thread. Sempre usar `QApplication.postEvent()`.

---

## Providers de Transportadoras

Cada transportadora tem um arquivo em `fretebot/providers/`. Todos herdam de `ProviderBase`:

```python
class ProviderBase(ABC):
    async def coteir(self, origem: str, destino: str, peso: float, valor: float) -> Cotacao | None:
        pass
```

| Transportadora | Método | Headless | Observação |
|---|---|---|---|
| BRASPRESS | Playwright | True | |
| BAUER | HTTP automático | - | |
| TRD | Playwright | True | |
| AGEX | Playwright | True | |
| EUCATUR | Playwright (SSW) | True | Portal SSW |
| COOPEX | Playwright (SSW) | True | Mesmo portal SSW da Eucatur |
| RODONAVES | Playwright | False | reCAPTCHA, usa stealth JS |
| ALFA | Playwright | False | Turnstile (login manual) |

**Stealth JS:** Rodonaves usa injeção de JS para parecer browser real e evitar reCAPTCHA.

**Pre-login:** Todas as transportadoras fazem pre-login em paralelo no startup do app para agilizar a primeira cotação.

---

## Configuração (`CONFIG.toml`)

Lido de (em ordem): `%APPDATA%\FreteBot\CONFIG.toml` → `{_MEIPASS}\CONFIG.toml` → pasta do script.

```toml
[fretebot]
github_repo = "owner/repo"           # Para o auto-updater
error_gist_id = "abc123"             # Gist para relatório de erros
error_report_token = "ghp_..."       # Token GitHub (só write:gist)

[transportadoras.braspress]
cnpj_remetente = "00.000.000/0000-00"
usuario = "..."
senha = "..."

[transportadoras.rodonaves]
cnpj = "..."
senha = "..."
# ... etc por transportadora
```

---

## Convenções de Código

- **Sempre usar `async/await`** para operações de browser (Playwright é assíncrono)
- **Nunca acessar widgets Qt de threads secundárias** — usar `QApplication.postEvent()`
- **Logging** sempre via `get_logger(__name__)` de `fretebot.logging_conf`
- **Erros de cotação** nunca devem derrubar o app — capturar exceção e retornar `None`
- **CEPs** sempre como string de 8 dígitos sem hífen
- **Valores monetários** como `float` em reais
- **Configuração** sempre lida do `CONFIG.toml`, nunca hardcoded no código
- **Versão** lida do arquivo `app/version.txt`

## O que NUNCA fazer

- ❌ Nunca hardcodar credenciais ou tokens no código
- ❌ Nunca chamar métodos de widget Qt diretamente de uma thread secundária
- ❌ Nunca deixar erro de cotação de uma transportadora derrubar as outras
- ❌ Nunca fazer `import *`
- ❌ Nunca usar `time.sleep()` em código async — usar `await asyncio.sleep()`
- ❌ Nunca expor chave de licença ou token nos logs

---

## Repositórios

- **`kaianesteffens/RomaneioBeta`** — repositório **privado** com o código-fonte completo
- **Repo de release** — repositório **público** usado apenas para publicar GitHub Releases (ZIPs de atualização). Nenhum código-fonte vai para lá.

## Processo de Release

1. Incrementar `app/version.txt`
2. Rodar `installer/build.bat` (gera `dist/FreteBot/`)
3. Rodar `installer/build_update_zip.bat` (gera `FreteBot-Update-X.Y.zip`)
4. Publicar o ZIP como asset em uma GitHub Release no repo público de release
5. O app detecta automaticamente e faz update silencioso no próximo startup

---

## 🔄 Auto-atualização destas instruções

**O Copilot DEVE atualizar este arquivo sempre que uma mudança significativa for feita no projeto.**

### Quando atualizar:
- Adicionou ou removeu uma transportadora em `fretebot/providers/`
- Criou, renomeou ou deletou um arquivo/pasta importante
- Mudou a arquitetura ou o fluxo de dados
- Adicionou um novo módulo principal (novo `.py` na raiz do `app/`)
- Mudou uma convenção de código ou regra do projeto
- Mudou o processo de build ou release
- Adicionou novos campos aos modelos de dados (`models.py`)
- Adicionou novos QEvents de comunicação entre threads

### Como atualizar:
1. Leia o arquivo atual (`.github/copilot-instructions.md`)
2. Identifique **somente a seção afetada** pela mudança
3. Reescreva apenas essa seção — não altere o resto
4. Mantenha o mesmo formato, estilo e língua portuguesa
5. Faça commit junto com as mudanças do código

### Exemplos de gatilhos:
- "Adicionei o provider JAMEF" → atualizar tabela de transportadoras
- "Renomeei `cotacao_transportadoras.py` para `cotador.py`" → atualizar estrutura de pastas
- "Adicionei campo `urgente: bool` no dataclass `Pedido`" → atualizar modelos de dados
- "Criei um novo QEvent `ErroFatalEvent`" → atualizar seção de thread-safe
