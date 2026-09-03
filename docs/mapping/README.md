# Work ledgers

Live work is one card per item under `planning/`, indexed by `governance/work_registry.json`
(`python tools/devctl.py status | next | render-map`). A finished card is deleted with its registry entry;
its history is the Git history of the change that finished it.
