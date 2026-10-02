@echo off
cd /d "%~dp0"
where py >nul 2>&1 && set "PY=py -3"
if not defined PY where python >nul 2>&1 && set "PY=python"
if not defined PY (
  echo Instale o Python 3 em https://www.python.org/downloads/
  echo Na instalacao, marque Add python.exe to PATH.
  pause
  exit /b 1
)
if not exist .venv\Scripts\python.exe (
  %PY% -m venv .venv
)
if exist .venv\Scripts\python.exe (
  .venv\Scripts\python.exe -m pip install -q -r requirements.txt
  .venv\Scripts\python.exe pagina.py
) else (
  %PY% -m pip install -q -r requirements.txt
  %PY% pagina.py
)
if errorlevel 1 pause
