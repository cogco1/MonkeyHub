# V3 ownership handover

V4 does not inherit V3 as a hidden runtime. It owns a provider-neutral,
building-neutral capability contract and may explicitly consult one frozen V3
implementation through a bounded read-only subprocess receipt.

For the first retained responsibility, load-path analysis:

- `archflow.capabilities.v3_diagnostic` is the single production contract
  owner.
- No native or legacy provider owns design, geometry, validation, persistence,
  world, or canonical-write authority.
- The V3 provider must be selected explicitly. Missing or failed providers
  return a named versioned receipt; they never fall back to a Pack, example
  planner, V2 stage, composer, or another provider.
- P013 probe records remain immutable oracle evidence. They prove only that the
  isolated diagnostic produced named semantic observations, not that a
  building was generated or is usable.
- Retiring V3 means reaching zero production authority. It does not mean
  deleting V3 source, Gold/Red cases, failures, or other evidence.

`python tools/check_v3_boundary.py` enforces this handover. It binds the
classified V3 revision and selected responsibility, checks for one contract
owner and zero writers, scans V4 production ASTs for forbidden imports and
symbols, prevents undeclared bridge consumers and machine-specific V3 paths,
and reloads the two frozen provider receipts by digest.

The quarantine must be updated deliberately when a later V3 responsibility is
retained. Adding a compatibility import, automatic fallback, second writer, or
generic V3 CLI is a boundary failure, not a migration shortcut.
