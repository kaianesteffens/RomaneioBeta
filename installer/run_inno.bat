@echo off
setlocal EnableDelayedExpansion

echo Procurando Inno Setup...

set ISCC=
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not defined ISCC (
    for /f "delims=" %%i in ('where ISCC.exe 2^>nul') do set "ISCC=%%i"
)

if not defined ISCC (
    echo ERRO: ISCC.exe nao encontrado!
    pause
    exit /b 1
)

echo Usando: %ISCC%

"%ISCC%" /DMyAppName="Romaneio Beta" /DMyAppVersion=2.2 /DMyOutputBaseFilename="Romaneio-Beta-Setup-2.2" /DMySetupIconFile="%~dp0assets\romaneio.ico" "%~dp0FreteBot-installer.iss"

if %ERRORLEVEL% neq 0 (
    echo ERRO: Inno Setup falhou!
    pause
    exit /b 1
)

echo.
echo [OK] Instalador Romaneio-Beta-Setup-2.2.exe gerado!
pause
