# Fretio — Como Gerar o Instalador Windows

## Pré-requisitos

1. **Windows 10/11** (64-bit)
2. **Python 3.11+** — [python.org/downloads](https://www.python.org/downloads/)
   - Marcar "Add Python to PATH" durante a instalação
3. **Inno Setup 6** — [jrsoftware.org/isdl.php](https://jrsoftware.org/isdl.php)
   - Instalar com os Language Packs (incluir "Brazilian Portuguese")

## Build Rápido (um clique)

```cmd
cd installer
build.bat
```

O script faz tudo automaticamente:
1. Instala dependências Python pelo lockfile (`requirements-lock.txt`)
2. Falha se o lockfile não existir, para evitar build com dependências soltas
3. Gera o executável com PyInstaller usando o ambiente instalado pelo lock (pasta `dist\Fretio\`)
4. Compila o instalador com Inno Setup (`installer\Fretio-Setup.exe`)

## Build Manual (passo a passo)

Rode os comandos a partir de `installer\`.

### 1. Instalar dependências

```cmd
cd installer
pip install --no-deps -r requirements-lock.txt
```

### 2. Gerar executável

```cmd
pyinstaller --clean --noconfirm Fretio.spec
```

Saída: `dist\Fretio\Fretio.exe` + dependências na mesma pasta.

O app não é distribuído com `CONFIG.toml`. A configuração por empresa é criada pelo
próprio app no primeiro uso, sem credenciais versionadas — não copie `CONFIG.toml`
para dentro do build.

### 3. Compilar instalador

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
│   └── Fretio\              (pacote Python)
│       └── CONFIG.example.toml
│
├── Instala (se ausente): Microsoft Edge WebView2 Runtime
│       via MicrosoftEdgeWebview2Setup.exe (a UI depende do WebView2)
│
├── Cria: %APPDATA%\Fretio\
│   └── cache\                 (cache de cotações)
│
├── Atalho: Menu Iniciar → Fretio
└── Atalho: Área de Trabalho (opcional)
```

## Após Instalar no Windows do Usuário

1. Executar o instalador `Fretio-Setup.exe` (instala o WebView2 Runtime se ele estiver ausente)
2. Executar Fretio pelo atalho no Menu Iniciar
3. Configurar a empresa e as credenciais pela própria interface do app

Para conferir se o Google Chrome está presente, execute `verificar_navegador.bat`
a partir de `installer\`. Ele apenas verifica a instalação do Chrome e não instala nada.

## Arquivos do Build

| Arquivo | Função |
|---|---|
| `Fretio.spec` | Configuração PyInstaller (one-folder, GUI, sem console) |
| `Fretio-installer.iss` | Script Inno Setup (instalador Windows) |
| `build.bat` | Script automatizado de build |
| `verificar_navegador.bat` | Verifica a presença do Google Chrome (não instala) |
| `MicrosoftEdgeWebview2Setup.exe` | Bootstrapper do WebView2 Runtime baixado para o build; instalado pelo instalador quando ausente |
| `requirements.in` | Dependências diretas mantidas por humanos |
| `requirements.txt` | Alias de compatibilidade para instalações locais (`-r requirements.in`) |
| `requirements-lock.txt` | Dependências Python congeladas consumidas pelo build reproduzível |

## Notas

- **UI / WebView2**: A interface roda em WebView2 via pywebview. O instalador instala o
  Microsoft Edge WebView2 Runtime quando ele está ausente (`MicrosoftEdgeWebview2Setup.exe`,
  baixado de <https://go.microsoft.com/fwlink/p/?LinkId=2124703>). No Windows 11 o runtime
  costuma já vir presente; em Windows 10 ou imagens corporativas pode faltar.
- **Google Chrome**: O Fretio usa o Google Chrome já instalado no computador (não empacota
  Chromium). `verificar_navegador.bat` só confere a presença do Chrome.
- **Modo GUI**: O .exe roda sem janela de console (`console=False` no PyInstaller).
- **Desinstalação**: Pelo Windows → Configurações → Apps → Fretio → Desinstalar.

## Atualizar o lockfile de dependências

Atualize o lockfile somente quando for intencional atualizar dependências do build.
O build oficial não regenera o lock e não usa `requirements.in` nem `requirements.txt`.

```cmd
cd installer
python-3.12\python.exe -m pip install -r requirements.in
python-3.12\python.exe -m pip freeze > requirements-lock.txt
python-3.12\python.exe -m pip install --no-deps -r requirements-lock.txt
build.bat
```

Depois confira no diff se `playwright==1.58.0`, `greenlet==3.1.1`, `cryptography==48.0.0` e `pyinstaller==6.20.0` só mudaram quando essa atualização foi deliberada.
Antes de publicar, teste um build limpo removendo `installer\python-3.12` e `installer\dist` para confirmar que o lockfile sozinho recria o ambiente.
