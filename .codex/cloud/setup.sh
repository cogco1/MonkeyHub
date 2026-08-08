#!/usr/bin/env bash
set -euo pipefail

python -m pip install --upgrade pip
python -m pip install -e .

if [[ ! -f config/runtime.json ]]; then
  cp config/runtime.codex-cloud.json config/runtime.json
fi

archflow-runtime --config config/runtime.json --repository-root . init
python tools/archcheck.py
python -m unittest discover -s tests -v

echo "Codex Cloud ready. Runtime data: /tmp/archflow-v4-runtime"
echo "The /tmp runtime is disposable; persistent evidence must be promoted explicitly."
