#!/usr/bin/env bash
set -euo pipefail

python -m pip install --upgrade pip
python -m pip install -e .

python tools/archcheck.py
python -m unittest discover -s tests -v

echo "Codex Cloud ready."
