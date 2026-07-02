@echo off
REM Roda o Fretio direto pelo Python (sem precisar rebuild)
cd /d "%~dp0"
set "PY=%~dp0python-3.12\python.exe"
set "PYTHONPATH=%~dp0;%~dp0fretio\src;%PYTHONPATH%"

"%PY%" -u web_app.py
pause
