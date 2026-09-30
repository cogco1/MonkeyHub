#!/usr/bin/env bash
set -euo pipefail

python -m pip install -e packages/archflow -e packages/monkeyarch -e packages/monkeydiagram \
  -e packages/monkeymonitor -e packages/monkeycontrol

python tools/governance/archcheck.py

echo "Codex Cloud maintenance checks passed."
