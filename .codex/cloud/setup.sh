#!/usr/bin/env bash
set -euo pipefail

python -m pip install --upgrade pip
python -m pip install -e . -e packages/archflow -e packages/monkeydiagram

python tools/archcheck.py
python -m unittest discover -s tests -v
python -m unittest discover -s packages/archflow/tests -v
python -m unittest discover -s packages/monkeydiagram/tests -v

echo "Codex Cloud ready."
