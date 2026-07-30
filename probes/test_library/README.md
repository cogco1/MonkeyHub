# Test library probe

This is a synthetic, non-authoritative building case used only to exercise the
current public ArchFlow boundary.

- `input/` contains the project request and externally supplied information.
- `runs/` contains only records and artifacts produced by the generic smoke
  runner.
- This directory contains no Architect implementation, design compiler,
  geometry script, expert schedule, validator, reducer, or loader.

The current smoke proves framework wiring only. It does not prove that the
library has been architecturally programmed, designed, built, or found usable.
