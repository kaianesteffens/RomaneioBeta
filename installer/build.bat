@echo off
setlocal EnableDelayedExpansion

REM ============================================================
REM Fretio — Build do Instalador Windows
REM NAO precisa instalar Python no sistema!
REM Baixa Python 3.12 embutido automaticamente.
REM Unico requisito: Inno Setup 6 (opcional, para gerar .exe)
REM ============================================================

echo.
echo ============================================================
echo  Fretio - Build do Instalador Windows
echo ============================================================
echo.

set "PYDIR=%~dp0python-3.12"
set "PY=%PYDIR%\python.exe"

if defined CI (
    set "FB_PAUSE_CMD=rem"
) else (
    set "FB_PAUSE_CMD=pause"
)

REM ── 1. Baixar Python embutido se nao existir ─────────────────
if not exist "%PY%" (
    echo [1/5] Baixando Python 3.12 embutido...

    REM Baixar via curl (disponivel no Win10+)
    curl -L -o "%~dp0python312.zip" "https://www.python.org/ftp/python/3.12.8/python-3.12.8-embed-amd64.zip"
    if %ERRORLEVEL% neq 0 (
        echo ERRO: Falha ao baixar Python. Verifique sua conexao.
        %FB_PAUSE_CMD%
        exit /b 1
    )

    REM Extrair
    mkdir "%PYDIR%" 2>nul
    powershell -Command "Expand-Archive -Path '%~dp0python312.zip' -DestinationPath '%PYDIR%' -Force"
    del "%~dp0python312.zip"

    REM Habilitar pip no Python embutido
    REM Remover limite de import do _pth file
    for %%f in ("%PYDIR%\python312._pth") do (
        powershell -Command "(Get-Content '%%f') -replace '#import site','import site' | Set-Content '%%f'"
    )

    REM Instalar pip
    curl -L -o "%PYDIR%\get-pip.py" "https://bootstrap.pypa.io/get-pip.py"
    "%PY%" "%PYDIR%\get-pip.py" --quiet
    del "%PYDIR%\get-pip.py"

    echo [OK] Python 3.12 pronto em: %PYDIR%
) else (
    echo [OK] Python 3.12 encontrado em: %PYDIR%
)

for /f "tokens=2" %%v in ('"%PY%" --version 2^>^&1') do set PYVER=%%v
echo      Versao: %PYVER%

REM ── 2. Instalar dependencias ─────────────────────────────────
echo.
echo [2/5] Instalando dependencias Python...
"%PY%" -m pip install --upgrade pip --quiet 2>nul
"%PY%" -m pip install -r requirements.txt --quiet
if %ERRORLEVEL% neq 0 (
    echo ERRO: Falha ao instalar dependencias!
    %FB_PAUSE_CMD%
    exit /b 1
)
"%PY%" -m pip install pyinstaller --quiet
echo [OK] Dependencias instaladas

REM ── 2.5. Definir versão do build (1.0, 1.1, 1.2, ...) ───────
set "VERSION_FILE=%~dp0..\app\version.txt"
for /f "usebackq tokens=*" %%v in ("%VERSION_FILE%") do set "CUR_VER=%%v"
if not defined CUR_VER set "CUR_VER=1.0"
for /f %%v in ('powershell -NoProfile -Command "$v='%CUR_VER%'.Trim(); $parts=$v.Split('.'); $major=$parts[0]; $minor=[int]$parts[1]+1; Write-Output \"$major.$minor\""') do set APP_VERSION=%%v
if not defined APP_VERSION (
    echo ERRO: Falha ao calcular a versão do build.
    %FB_PAUSE_CMD%
    exit /b 1
)
>"%VERSION_FILE%" echo !APP_VERSION!
set "APP_NAME=Fretio"
set "OUTPUT_BASENAME=Fretio-Setup-!APP_VERSION!"
echo [OK] Versao deste build: !APP_VERSION!

REM ── 3. Fechar Fretio se estiver rodando ──────────────────────────────
echo.
tasklist /FI "IMAGENAME eq Fretio.exe" 2>nul | find /I "Fretio.exe" >nul
if %ERRORLEVEL% == 0 (
    echo [AVISO] Fretio.exe esta rodando. Fechando...
    taskkill /IM Fretio.exe /F >nul 2>&1
    timeout /t 3 /nobreak >nul
)
REM Limpar dist anterior
if exist "dist\Fretio" (
    rmdir /S /Q "dist\Fretio" 2>nul
    timeout /t 2 /nobreak >nul
)

REM ── 4. Gerar executavel com PyInstaller ──────────────────────
echo.
echo [4/5] Gerando executavel com PyInstaller...
"%PY%" -m PyInstaller --clean --noconfirm Fretio.spec
if %ERRORLEVEL% neq 0 (
    echo ERRO: PyInstaller falhou!
    %FB_PAUSE_CMD%
    exit /b 1
)
if exist "dist\Fretio\Fretio.exe" (
    copy /Y "dist\Fretio\Fretio.exe" "dist\Fretio\FreteBot.exe" >nul
    echo [OK] Alias legado gerado: dist\Fretio\FreteBot.exe
)
echo [OK] Executavel gerado em dist\Fretio\

REM ── 5. Garantir arquivos de configuracao no dist ─────────────────
if not exist "dist\Fretio\_internal\CONFIG.example.toml" (
    if exist "%~dp0..\app\CONFIG.example.toml" (
        copy /Y "%~dp0..\app\CONFIG.example.toml" "dist\Fretio\_internal\CONFIG.example.toml" >nul
        echo [OK] CONFIG.example.toml copiado para dist\_internal
    ) else (
        echo [AVISO] app\CONFIG.example.toml nao encontrado.
    )
) else (
    echo [OK] CONFIG.example.toml ja presente em dist\_internal
)

if not exist "dist\Fretio\_internal\CONFIG.toml" (
    if exist "%~dp0..\app\CONFIG.toml" (
        copy /Y "%~dp0..\app\CONFIG.toml" "dist\Fretio\_internal\CONFIG.toml" >nul
        echo [OK] CONFIG.toml copiado de app para dist\_internal
    ) else if exist "dist\Fretio\_internal\CONFIG.example.toml" (
        copy /Y "dist\Fretio\_internal\CONFIG.example.toml" "dist\Fretio\_internal\CONFIG.toml" >nul
        echo [OK] CONFIG.toml gerado a partir de CONFIG.example.toml
    ) else (
        echo [AVISO] Nao foi possivel gerar dist\Fretio\_internal\CONFIG.toml
    )
) else (
    echo [OK] CONFIG.toml ja presente em dist\_internal
)

echo [OK] Normalizando defaults obrigatorios do CONFIG embutido...
"%PY%" normalize_embedded_config.py "dist\Fretio\_internal\CONFIG.toml"
if %ERRORLEVEL% neq 0 (
    echo ERRO: Falha ao normalizar dist\Fretio\_internal\CONFIG.toml
    %FB_PAUSE_CMD%
    exit /b 1
)
if exist "dist\Fretio\CONFIG.toml" (
    "%PY%" normalize_embedded_config.py "dist\Fretio\CONFIG.toml"
    if %ERRORLEVEL% neq 0 (
        echo ERRO: Falha ao normalizar dist\Fretio\CONFIG.toml
        %FB_PAUSE_CMD%
        exit /b 1
    )
)
echo [OK] CONFIG embutido validado

REM ── 6. Compilar instalador com Inno Setup ────────────────────
echo.
echo [5/5] Compilando instalador (Inno Setup)...

set ISCC=
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" (
    set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
) else if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" (
    set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
) else if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" (
    set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
) else (
    echo.
    echo ============================================================
    echo  AVISO: Inno Setup 6 nao encontrado.
    echo  O executavel foi gerado em: dist\Fretio\Fretio.exe
    echo  Voce pode rodar direto daqui!
    echo.
    echo  Para criar o instalador .exe, instale o Inno Setup:
    echo  https://jrsoftware.org/isdl.php
    echo  Depois execute: build.bat novamente
    echo ============================================================
    goto :skip_inno
)

"%ISCC%" /DMyAppName="!APP_NAME!" /DMyAppVersion=!APP_VERSION! /DMyOutputBaseFilename="!OUTPUT_BASENAME!" /DMySetupIconFile="%~dp0assets\romaneio.ico" Fretio-installer.iss
if %ERRORLEVEL% neq 0 (
    echo ERRO: Inno Setup falhou!
    %FB_PAUSE_CMD%
    exit /b 1
)
echo [OK] Instalador gerado!
set "INSTALLER_PATH=installer\!OUTPUT_BASENAME!.exe"

:skip_inno

REM ── 6. Compilar launcher universal (Romaneio.exe) ────────────
echo.
echo [6/6] Compilando launcher universal (Romaneio.exe)...

REM Em CI, o launcher e gerado em etapa dedicada do workflow
if defined CI goto :skip_launcher

REM Usa Python do sistema (nao o embutido), pois precisa de tkinter
set SYSPY=
where python >nul 2>&1
if %ERRORLEVEL% == 0 (
    set "SYSPY=python"
) else (
    where python3 >nul 2>&1
    if %ERRORLEVEL% == 0 set "SYSPY=python3"
)

if not defined SYSPY (
    echo [AVISO] Python do sistema nao encontrado. Romaneio.exe nao foi gerado.
    echo         Instale Python 3.10+ e execute build.bat novamente.
    goto :skip_launcher
)

REM Garante que pyinstaller esta disponivel no Python do sistema
%SYSPY% -m pip install pyinstaller --quiet --disable-pip-version-check
%SYSPY% -m PyInstaller --clean --noconfirm launcher.spec
if %ERRORLEVEL% neq 0 (
    echo [AVISO] Falha ao compilar Romaneio.exe. O Fretio principal foi gerado normalmente.
    goto :skip_launcher
)

REM Mover para pasta installer/ para facilitar distribuicao
if exist "dist\Romaneio.exe" (
    if not exist "installer" mkdir "installer"
    copy /Y "dist\Romaneio.exe" "installer\Romaneio.exe" >nul
    echo [OK] Launcher gerado: installer\Romaneio.exe
)

:skip_launcher
echo.
echo ============================================================
echo  BUILD CONCLUIDO!
echo.
echo  App:         !APP_NAME! !APP_VERSION!
echo  Executavel:  dist\Fretio\Fretio.exe
if exist "installer\!OUTPUT_BASENAME!.exe" (
    echo  Instalador:  installer\!OUTPUT_BASENAME!.exe
)
if exist "installer\Romaneio.exe" (
    echo  Launcher:    installer\Romaneio.exe
)
echo ============================================================
echo.
%FB_PAUSE_CMD%

endlocal
