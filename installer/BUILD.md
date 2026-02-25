# FreteBot — Como Gerar o Instalador Windows

## Pré-requisitos

1. **Windows 10/11** (64-bit)
2. **Python 3.11+** — [python.org/downloads](https://www.python.org/downloads/)
   - Marcar "Add Python to PATH" durante a instalação
3. **Inno Setup 6** — [jrsoftware.org/isdl.php](https://jrsoftware.org/isdl.php)
   - Instalar com os Language Packs (incluir "Brazilian Portuguese")

## Build Rápido (um clique)

```cmd
cd _release_stage\app
build.bat
```

O script faz tudo automaticamente:
1. Instala dependências Python (`pip install -r requirements.txt`)
2. Instala PyInstaller
3. Instala Chromium via Playwright
4. Gera o executável com PyInstaller (pasta `dist\FreteBot\`)
5. Compila o instalador com Inno Setup (`installer\FreteBot-Setup.exe`)

## Build Manual (passo a passo)

### 1. Instalar dependências

```cmd
cd _release_stage\app
pip install -r requirements.txt
pip install pyinstaller
python -m playwright install chromium
```

### 2. Gerar executável

```cmd
pyinstaller --clean --noconfirm FreteBot.spec
```

Saída: `dist\FreteBot\FreteBot.exe` + dependências na mesma pasta.

### 3. Copiar CONFIG.toml (se tiver credenciais)

```cmd
copy fretebot\CONFIG.toml dist\FreteBot\fretebot\CONFIG.toml
```

### 4. Compilar instalador

```cmd
"%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" FreteBot-installer.iss
```

Saída: `installer\FreteBot-Setup.exe`

## Estrutura do Instalador

```
FreteBot-Setup.exe
├── Instala em: C:\Program Files\FreteBot\
│   ├── FreteBot.exe           (aplicação principal)
│   ├── *.dll / *.pyd          (dependências Python empacotadas)
│   ├── fretebot\              (pacote Python)
│   │   └── CONFIG.example.toml
│   └── instalar_navegador.bat (instala Chromium pós-instalação)
│
├── Cria: %APPDATA%\FreteBot\
│   ├── CONFIG.toml            (copiado do example na 1ª instalação)
│   └── cache\                 (cache de cotações)
│
├── Atalho: Menu Iniciar → FreteBot
└── Atalho: Área de Trabalho (opcional)
```

## Após Instalar no Windows do Usuário

1. Executar o instalador `FreteBot-Setup.exe`
2. Na tela final, marcar "Instalar navegador Chromium" → executa `instalar_navegador.bat`
3. Editar `%APPDATA%\FreteBot\CONFIG.toml` com as credenciais reais
4. Executar FreteBot pelo atalho no Menu Iniciar

## Arquivos do Build

| Arquivo | Função |
|---|---|
| `FreteBot.spec` | Configuração PyInstaller (one-folder, GUI, sem console) |
| `FreteBot-installer.iss` | Script Inno Setup (instalador Windows) |
| `build.bat` | Script automatizado de build |
| `instalar_navegador.bat` | Instala Chromium após instalação |
| `requirements.txt` | Dependências Python |

## Notas

- **Playwright/Chromium**: O navegador Chromium (~150MB) é instalado separadamente pelo `instalar_navegador.bat`. Não é empacotado dentro do .exe para manter o instalador leve.
- **CONFIG.toml**: Contém credenciais das transportadoras. Nunca é sobrescrito em atualizações (flag `onlyifdoesntexist` no Inno Setup).
- **Modo GUI**: O .exe roda sem janela de console (`console=False` no PyInstaller).
- **Desinstalação**: Pelo Windows → Configurações → Apps → FreteBot → Desinstalar.
