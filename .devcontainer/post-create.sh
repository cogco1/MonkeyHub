#!/usr/bin/env bash
set -euo pipefail

python -m pip install --upgrade pip
python -m pip install -e .

if [[ ! -f config/runtime.json ]]; then
  cp config/runtime.codespaces.json config/runtime.json
fi

archflow-runtime --config config/runtime.json --repository-root . init
python tools/archcheck.py
python -m unittest discover -s tests -v

echo "Codespaces ready. Runtime data: /workspaces/archflow-v4-runtime"
