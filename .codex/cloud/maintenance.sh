#!/usr/bin/env bash
set -euo pipefail

python -m pip install -e .

python tools/archcheck.py

echo "Codex Cloud maintenance checks passed."
