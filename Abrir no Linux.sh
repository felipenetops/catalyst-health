#!/bin/bash
cd "$(dirname "$0")"
if ! command -v python3 >/dev/null 2>&1; then
  echo "Instale o Python 3 e tente de novo."
  read -r _
  exit 1
fi
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv >/dev/null 2>&1 || true
fi
if [ -x .venv/bin/python ]; then
  PY=".venv/bin/python"
else
  PY="python3"
fi
"$PY" -m pip install -q -r requirements.txt
"$PY" pagina.py
