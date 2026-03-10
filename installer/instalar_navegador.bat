@echo off
REM FreteBot - Verificar Google Chrome
REM O FreteBot usa o Google Chrome instalado no computador (nao usa Chromium)

echo ============================================
echo FreteBot - Verificando Google Chrome...
echo ============================================
echo.

REM Verificar se o Chrome esta instalado em algum caminho padrao
set "FOUND="
if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" set "FOUND=1"
if exist "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" set "FOUND=1"
if exist "%LocalAppData%\Google\Chrome\Application\chrome.exe" set "FOUND=1"

if defined FOUND (
    echo Google Chrome encontrado!
    echo O FreteBot esta pronto para uso.
) else (
    echo ATENCAO: Google Chrome NAO encontrado!
    echo.
    echo O FreteBot requer o Google Chrome instalado.
    echo Baixe em: https://www.google.com/chrome/
    echo.
    echo Apos instalar o Chrome, o FreteBot funcionara normalmente.
)

echo.
echo ============================================
echo Verificacao concluida.
echo ============================================
pause
