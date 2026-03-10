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
- **Versão atual:** lida de `app/version.txt` (ex: `1.50`)

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
- **`kaianesteffens/RomaneioBeta-releases`** — repositório **público** usado apenas para publicar GitHub Releases (ZIPs de atualização). Nenhum código-fonte vai para lá.
- **Diretório local de build:** `C:\\Users\\eduar_zgrj9vh\\Desktop\\FRETEBOT\\FreteBot-Installer`
- **GitHub CLI** (`gh`) está instalado e autenticado como `kaianesteffens` com escopo `repo`, `gist`, `workflow`

### Workspace virtual vs diretório local
O workspace do VS Code (`vscode-vfs://github/kaianesteffens/RomaneioBeta`) é um **filesystem virtual** montado pelo GitHub. Edições feitas nele atualizam o repositório GitHub mas **NÃO** afetam o diretório local. Para fazer build, é necessário aplicar as mesmas mudanças no diretório local (`C:\\Users\\eduar_zgrj9vh\\Desktop\\FRETEBOT\\FreteBot-Installer`) usando terminal PowerShell.

## Processo de Build e Release (passo a passo exato)

O **workspace do VS Code** (`vscode-vfs://github/...`) é um filesystem **virtual** do GitHub. Ele **não é** o diretório local. Para builds, o clone local fica em `C:\Users\eduar_zgrj9vh\Desktop\FRETEBOT\FreteBot-Installer`.

### Fluxo completo de release

1. **Aplicar mudanças no diretório local** — Se as edições foram feitas no workspace virtual, copiar as alterações para os mesmos arquivos no diretório local. Usar Python ou PowerShell para aplicar as edições nos arquivos locais.

2. **Executar build** — Navegar até `installer/` e rodar:
   ```
   cd C:\Users\eduar_zgrj9vh\Desktop\FRETEBOT\FreteBot-Installer\installer
   cmd /c "build.bat"
   ```
   O `build.bat` **auto-incrementa** a versão minor em `app/version.txt` (ex: 1.49 → 1.50). Não editar `version.txt` manualmente antes do build.

3. **Gerar ZIP de atualização**:
   ```
   cmd /c "build_update_zip.bat"
   ```
   O ZIP é gerado em `installer\installer\FreteBot-Update-X.Y.zip` (caminho aninhado — `installer` dentro de `installer`).

4. **Commit e push** — Voltar ao root do repo e fazer commit + push:
   ```
   cd C:\Users\eduar_zgrj9vh\Desktop\FRETEBOT\FreteBot-Installer
   git add -A
   git commit -m "vX.Y: descrição das mudanças"
   git push origin master
   ```
   ⚠️ Se o push falhar com "fetch first", fazer `git pull --rebase origin master` antes de re-tentar.

5. **Publicar GitHub Release** — Usar GitHub CLI (`gh`) para publicar no repo **público**:
   ```
   gh release create "vX.Y" --repo "kaianesteffens/RomaneioBeta-releases" --title "Romaneio Beta vX.Y" --latest --notes "descrição"
   gh release upload "vX.Y" "C:\...\installer\installer\FreteBot-Update-X.Y.zip" --repo "kaianesteffens/RomaneioBeta-releases"
   ```
   - O repo de release é `kaianesteffens/RomaneioBeta-releases` (público, separado do código-fonte).
   - Antes de criar, verificar se já existe draft/tag com `gh release list --repo kaianesteffens/RomaneioBeta-releases`.
   - Se existir tag/release anterior com mesmo nome, deletar com `gh release delete vX.Y --yes` e `gh api -X DELETE repos/kaianesteffens/RomaneioBeta-releases/git/refs/tags/vX.Y`.

6. O app detecta automaticamente e faz update silencioso no próximo startup.

### Verificações pós-build
- `Test-Path "installer\dist\FreteBot\FreteBot.exe"` deve retornar True
- `Get-Content "app\version.txt"` deve mostrar a nova versão
- `gh release view "vX.Y" --repo kaianesteffens/RomaneioBeta-releases` deve mostrar o asset ZIP

---

## Relatório de Erros (Error Reporter)

Erros do app são enviados automaticamente como **comentários** em um GitHub Gist secreto.

- **Gist ID:** `***REMOVED-GIST-ID***`
- **Para ler erros em produção:** `gh api gists/***REMOVED-GIST-ID***/comments`
- Cada comentário contém: tipo de exceção, traceback, versão, hash da máquina, licença parcial, fingerprint
- Rate-limit: máximo 1 report por erro idêntico a cada 10 minutos
- O `error_reporter.py` usa `toml.load(path)` (aceita path direto) com fallback para `tomli` (requer `open(path, "rb")`)
- **Atenção:** `tomli.load()` ≠ `toml.load()` — `tomli` exige file object aberto em modo binário (`rb`), enquanto `toml` aceita path string

---

## Padrões Importantes e Bugs Conhecidos

### Portal SSW (Eucatur e COOPEX)
Eucatur e COOPEX compartilham o **mesmo código base** (portal SSW em `sistema.ssw.inf.br`). Ambos os providers (`eucatur.py` e `coopex.py`) têm estrutura quase idêntica.

**Padrão obrigatório para listeners:**
```python
# CORRETO — sempre usar try/finally para remover listener
handler = lambda r: asyncio.ensure_future(capture_response(r))
page.on('response', handler)
try:
    result = await self._submeter_e_extrair_inner(page, xml_responses)
finally:
    page.remove_listener('response', handler)
```
Nunca usar `page.on('response', ...)` sem `page.remove_listener()` no finally — causa **leak de handlers** que acumula a cada cotação.

**Regex para erros XML do SSW:**
```python
# CORRETO — [^<]+ captura acentos e espaços
re.search(r'<erro>([^<]+)</erro>', xml)

# ERRADO — \w+ não captura caracteres acentuados
re.search(r'<erro>(\w+)</erro>', xml)
```

### Provider __init__.py
`BraspressPlaywrightProvider` é importado como alias `BraspressProvider`. O `__all__` deve exportar apenas `BraspressProvider` (o alias), não o nome original.

### config.py
O `config.py` do pacote `fretebot` **não usa `tomllib`**. A leitura do CONFIG.toml é feita em `fretebot/config.py` (dataclass simples) e nos módulos que precisam (`error_reporter.py`, `updater.py`, etc.) usando o pacote `toml`.

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
