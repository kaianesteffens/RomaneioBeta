@echo off
REM Roda o FreteBot direto pelo Python (sem precisar rebuild)
cd /d "%~dp0"
set "PY=%~dp0python-3.12\python.exe"
set "PYTHONPATH=%~dp0;%~dp0fretebot\src;%PYTHONPATH%"

"%PY%" -u romaneio_app.py
pause
