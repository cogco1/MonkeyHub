#!/usr/bin/env bash
set -euo pipefail

python -m pip install -e .

if [[ ! -f config/runtime.json ]]; then
  cp config/runtime.codex-cloud.json config/runtime.json
fi

archflow-runtime --config config/runtime.json --repository-root . init
python tools/archcheck.py

echo "Codex Cloud maintenance checks passed."
