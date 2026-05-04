@echo off
setlocal EnableDelayedExpansion

echo Procurando Inno Setup...

set ISCC=
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if not defined ISCC (
    for /f "delims=" %%i in ('where ISCC.exe 2^>nul') do set "ISCC=%%i"
)

if not defined ISCC (
    echo ERRO: ISCC.exe nao encontrado!
    pause
    exit /b 1
)

echo Usando: %ISCC%

REM Ler versão de version.txt (evita hardcode)
set "APP_VERSION=2.9"
set "VERSION_FILE=%~dp0..\app\version.txt"
if exist "%VERSION_FILE%" (
    for /f "usebackq tokens=*" %%v in ("%VERSION_FILE%") do set "APP_VERSION=%%v"
)
echo Versao: %APP_VERSION%

set "ICON_FILE=%~dp0..\app\assets\romaneio.ico"
if not exist "%ICON_FILE%" set "ICON_FILE=%~dp0assets\romaneio.ico"

"%ISCC%" /DMyAppName="Fretio" /DMyAppVersion=%APP_VERSION% /DMyOutputBaseFilename="Fretio-Setup-%APP_VERSION%" /DMySetupIconFile="%ICON_FILE%" "%~dp0Fretio-installer.iss"

if %ERRORLEVEL% neq 0 (
    echo ERRO: Inno Setup falhou!
    pause
    exit /b 1
)

echo.
echo [OK] Instalador Fretio-Setup-%APP_VERSION%.exe gerado!
pause
