# Fretio — Como Gerar o Instalador Windows

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
1. Instala dependências Python pelo lockfile (`requirements-lock.txt`)
2. Usa `requirements.txt` como fallback se o lockfile não existir
3. Instala Chromium via Playwright
4. Gera o executável com PyInstaller (pasta `dist\Fretio\`)
5. Compila o instalador com Inno Setup (`installer\Fretio-Setup.exe`)

## Build Manual (passo a passo)

### 1. Instalar dependências

```cmd
cd _release_stage\app
pip install --no-deps -r requirements-lock.txt
python -m playwright install chromium
```

### 2. Gerar executável

```cmd
pyinstaller --clean --noconfirm Fretio.spec
```

Saída: `dist\Fretio\Fretio.exe` + dependências na mesma pasta.

### 3. Copiar CONFIG.toml (se tiver credenciais)

```cmd
copy Fretio\CONFIG.toml dist\Fretio\Fretio\CONFIG.toml
```

### 4. Compilar instalador

```cmd
"%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" Fretio-installer.iss
```

Saída: `installer\Fretio-Setup.exe`

## Estrutura do Instalador

```
Fretio-Setup.exe
├── Instala em: C:\Program Files\Fretio\
│   ├── Fretio.exe           (aplicação principal)
│   ├── *.dll / *.pyd          (dependências Python empacotadas)
│   ├── Fretio\              (pacote Python)
│   │   └── CONFIG.example.toml
│   └── instalar_navegador.bat (instala Chromium pós-instalação)
│
├── Cria: %APPDATA%\Fretio\
│   ├── CONFIG.toml            (copiado do example na 1ª instalação)
│   └── cache\                 (cache de cotações)
│
├── Atalho: Menu Iniciar → Fretio
└── Atalho: Área de Trabalho (opcional)
```

## Após Instalar no Windows do Usuário

1. Executar o instalador `Fretio-Setup.exe`
2. Na tela final, marcar "Instalar navegador Chromium" → executa `instalar_navegador.bat`
3. Editar `%APPDATA%\Fretio\CONFIG.toml` com as credenciais reais
4. Executar Fretio pelo atalho no Menu Iniciar

## Arquivos do Build

| Arquivo | Função |
|---|---|
| `Fretio.spec` | Configuração PyInstaller (one-folder, GUI, sem console) |
| `Fretio-installer.iss` | Script Inno Setup (instalador Windows) |
| `build.bat` | Script automatizado de build |
| `instalar_navegador.bat` | Instala Chromium após instalação |
| `requirements.txt` | Dependências Python diretas/fallback |
| `requirements-lock.txt` | Dependências Python congeladas para build reproduzível |

## Notas

- **Playwright/Chromium**: O navegador Chromium (~150MB) é instalado separadamente pelo `instalar_navegador.bat`. Não é empacotado dentro do .exe para manter o instalador leve.
- **CONFIG.toml**: Contém credenciais das transportadoras. Nunca é sobrescrito em atualizações (flag `onlyifdoesntexist` no Inno Setup).
- **Modo GUI**: O .exe roda sem janela de console (`console=False` no PyInstaller).
- **Desinstalação**: Pelo Windows → Configurações → Apps → Fretio → Desinstalar.

## Atualizar o lockfile de dependências

Atualize o lockfile somente quando for intencional atualizar dependências do build.

```cmd
cd installer
python-3.12\python.exe -m pip install -r requirements.txt pyinstaller==6.20.0
python-3.12\python.exe -m pip freeze > requirements-lock.txt
python-3.12\python.exe -m pip install --no-deps -r requirements-lock.txt
build.bat
```

Depois confira no diff se `playwright==1.58.0`, `greenlet==3.1.1` e `pyinstaller==6.20.0` só mudaram quando essa atualização foi deliberada.
