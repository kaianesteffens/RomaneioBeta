@echo off
REM FreteBot — Instalar navegador Chromium (Playwright)
REM Execute uma vez após a instalação

echo ============================================
echo FreteBot - Instalando navegador Chromium...
echo ============================================
echo.

REM Definir caminho dos browsers dentro do app empacotado
set "APPDIR=%~dp0"
set "DRIVER_DIR=%APPDIR%_internal\playwright\driver"
set "BROWSERS_DIR=%DRIVER_DIR%\package\.local-browsers"
set "PLAYWRIGHT_BROWSERS_PATH=%BROWSERS_DIR%"

REM Usar node.exe + cli.js do app empacotado
set "NODE_EXE=%DRIVER_DIR%\node.exe"
set "CLI_JS=%DRIVER_DIR%\package\cli.js"

if exist "%NODE_EXE%" if exist "%CLI_JS%" (
    echo Usando Playwright CLI do app...
    "%NODE_EXE%" "%CLI_JS%" install chromium
    goto :done
)

REM Fallback: tentar via python do sistema
where python >nul 2>&1
if %ERRORLEVEL% == 0 (
    python -m playwright install chromium
    goto :done
)

echo ERRO: Playwright CLI nao encontrado.
echo Reinstale o FreteBot ou entre em contato com o suporte.

:done
echo.
echo ============================================
echo Instalacao do navegador concluida!
echo Voce pode fechar esta janela.
echo ============================================
pause
