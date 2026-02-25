@echo off
setlocal EnableDelayedExpansion

REM ============================================================
REM  FreteBot — Gerar ZIP de Atualização para GitHub Release
REM  Roda APÓS o build.bat ter gerado dist\FreteBot\
REM  Gera: installer\FreteBot-Update-X.Y.zip
REM ============================================================

echo.
echo ============================================================
echo  FreteBot - Gerar ZIP de Atualização
echo ============================================================
echo.

set "DIST_DIR=%~dp0dist\FreteBot"
set "VERSION_FILE=%~dp0..\app\version.txt"

REM Verificar se dist existe
if not exist "%DIST_DIR%\FreteBot.exe" (
    echo ERRO: dist\FreteBot\FreteBot.exe nao encontrado.
    echo Execute build.bat primeiro!
    pause
    exit /b 1
)

REM Ler versão
for /f "usebackq tokens=*" %%v in ("%VERSION_FILE%") do set "APP_VERSION=%%v"
if not defined APP_VERSION set "APP_VERSION=0.0"
echo Versao: !APP_VERSION!

set "ZIP_NAME=FreteBot-Update-!APP_VERSION!.zip"
set "ZIP_PATH=%~dp0installer\!ZIP_NAME!"

REM Remover ZIP anterior se existir
if exist "%ZIP_PATH%" del "%ZIP_PATH%"

REM Criar ZIP com todo o conteudo de dist\FreteBot\
echo Compactando distribuicao...
powershell -NoProfile -Command "Compress-Archive -Path '%DIST_DIR%\*' -DestinationPath '%ZIP_PATH%' -Force"

if %ERRORLEVEL% neq 0 (
    echo ERRO: Falha ao criar ZIP!
    pause
    exit /b 1
)

REM Mostrar tamanho
for %%F in ("%ZIP_PATH%") do set "ZIP_SIZE=%%~zF"
set /a "ZIP_MB=!ZIP_SIZE! / 1048576"

echo.
echo ============================================================
echo  ZIP de atualizacao gerado!
echo.
echo  Arquivo:  !ZIP_PATH!
echo  Tamanho:  ~!ZIP_MB! MB
echo  Versao:   !APP_VERSION!
echo.
echo  Para publicar:
echo  1. Va em https://github.com/SEU-USUARIO/SEU-REPO/releases
echo  2. Clique "Draft a new release"
echo  3. Tag: !APP_VERSION!
echo  4. Anexe o arquivo: !ZIP_NAME!
echo  5. Publique a release
echo ============================================================
echo.
pause

endlocal
