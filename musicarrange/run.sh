#!/bin/sh
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
  .venv/bin/pip install -r requirements.txt
fi
if [ "$#" -eq 0 ]; then
  set -- demo
fi
exec .venv/bin/python -m nova "$@"
