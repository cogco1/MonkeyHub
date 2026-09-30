#!/usr/bin/env bash
set -euo pipefail

python -m pip install --upgrade pip
python -m pip install -e packages/archflow -e packages/monkeyarch -e packages/monkeydiagram \
  -e packages/monkeymonitor -e packages/monkeycontrol

python tools/governance/archcheck.py
python -m unittest discover -s tests -t . -v
python -m unittest discover -s tools/tests -v
python -m unittest discover -s packages/archflow/tests -v
python -m unittest discover -s packages/monkeyarch/tests -v
python -m unittest discover -s packages/monkeydiagram/tests -v
python -m unittest discover -s packages/monkeymonitor/tests -v
python -m unittest discover -s packages/monkeycontrol/tests -v

echo "Codex Cloud ready."
