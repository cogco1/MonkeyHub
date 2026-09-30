#!/usr/bin/env bash
set -euo pipefail

python -m pip install -e . -e packages/archflow -e packages/monkeyarch -e packages/monkeydiagram \
  -e packages/monkeymonitor

python tools/archcheck.py

echo "Codex Cloud maintenance checks passed."
