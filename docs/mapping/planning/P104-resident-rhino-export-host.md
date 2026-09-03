# P104 — Resident Rhino export host

**Status:** ready, annotated 2026-09-02 night: the native OCCT lane (P107) was decided; if it lands with its measured wall clocks, this card's value shrinks to one 37-second certification per stage gate and it will likely be retired unbuilt. Nobody starts this before P107's measurements exist.
**Lane:** productization and componentization
**Depends on:** P103 (the patch path and its oracle), P089 (the runner's export step)
**Retires:** one COM process per export. The per-export bridge stays as the fallback and as the equivalence oracle.

## Why

P103 made the *work* incremental and proved it: a change that touches eight objects rebuilds eight, carries the
rest by name, and reads back identical to a full rebuild. The wall clock did not move. Every measured path costs
about the same:

| Path | Objects rebuilt | Wall clock |
|---|---|---|
| Rocca envelope, full rebuild | 18 | 37.9 s |
| Rocca envelope, patch | 8 | 37.2 s |
| Rocca structure, restamp | 0 | 37.5 s |
| Villa envelope, patch with a block family | 27 | 37.3 s |

The variable part is small enough to disappear. What is left is fixed: `build_rhino_com_powershell_source` starts
an STA worker, activates `Rhino.Application.8`, waits for `IsInitialized`, runs one script, then an independent
cleanup terminates the process it owned. That start and teardown is the ~30 s, and it is paid once per seat per
export.

## Mechanism

1. **A host that outlives one export.** Keep the COM worker alive across the exports of a run: open it once,
   feed it script paths, read one completion marker per script, close it at the end of the run. The existing
   per-export bridge becomes the fallback.
2. **Ownership stays exact.** The current host witness proves the process the worker owns (`new_pid_count == 1`,
   `ownership_status == "exact"`). A resident host must prove the same thing once and then prove *the same*
   process on every subsequent script, so a stray Rhino can never be borrowed mid-run.
3. **Document isolation between exports.** Each export must start from the document its plan says it starts from
   (empty for a rebuild, the prior file for a patch). A resident host must clear and re-establish that per script,
   and fail typed rather than inherit the previous export's document.
4. **Failure containment.** One failed script must not poison the rest of the run: the host reports the failure,
   and the runner decides whether to continue with a fresh host or stop.
5. **The oracle stays.** A resident-host export must read back identically to a per-process export of the same
   program; the first runs of every project compare both.

## Acceptance

- [ ] A resident host runs every seat export of one run in one Rhino process, with exact ownership proven once and
      re-proven per script.
- [ ] Second and later exports in a run cost materially less than the first; measured on the villa west band and on
      Rocca, both reported as a table, not as a claim.
- [ ] Per-process export remains available and is what the oracle uses; a host failure falls back to it with a
      typed receipt saying so.
- [ ] Documents do not leak between exports: a patch that should start from the prior file, and a rebuild that
      should start empty, both do, and a violation fails typed.
- [ ] Full unittest suite and the architecture firewall pass.

## Do-not-do

No shared host across *runs* until ownership and document isolation are proven within one run. No silent reuse of
an already-running Rhino the harness did not start. No relaxation of the readback denominator to make the host
look faster.
