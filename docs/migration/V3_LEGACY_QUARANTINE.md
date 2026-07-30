# V3 legacy quarantine

V3 is frozen evidence and capability source material, not a V4 dependency.
P011 classifies responsibility slices before any boundary or pilot is built.
The machine-readable authority is
[`governance/v3_legacy_manifest.json`](../../governance/v3_legacy_manifest.json).

## Ownership rule

A V3 directory or Pack is not a migration unit. Facts, algorithms, defaults,
geometry, validation, and orchestration that happen to share a directory keep
separate entries and dispositions. Nothing in the manifest authorizes a source
copy, a Python import, a canonical write, or a world mutation.

The live retirement audit was captured at V3 commit
`3e70a4d23adb9c77e09fd230e38260389d44b8cd`. The checkout also had 22 tracked
changes, recorded by a separate status digest, so the commit is not
misrepresented as a clean source snapshot. The operative retirement-surface
fingerprint is the captured audit digest. The audit reports:

- four production `compose_building` call surfaces;
- eleven keyword-route surfaces;
- three legacy-adapter surfaces.

All eighteen findings map exactly once to a responsibility entry. Full
composers, production keyword routing, legacy adapters, and the LLM example
fallback are forbidden in production. Example fixtures, learning experiments,
Gold/Red records, and probes remain evidence or oracles. Building-specific Pack
content is a dossier, never a framework law.

## Selected pilot

P013 may pilot only `v3.gate.load_path_analysis`. Its candidate responsibility
is the building-neutral support graph and load-path analysis in
`archflow/brain/making/load_trace/__init__.py`.

The future boundary must:

- accept a neutral, versioned component graph;
- return detached findings and support paths;
- remain read-only and fail closed;
- avoid Pack registration, keyword routing, and full-building composition;
- leave acceptance, repair decisions, and canonical writes in V4.

Selection does not mean the V3 implementation is already a V4 capability.
P012 must first establish a versioned quarantined CLI, and P013 must prove the
exact pilot contract against fixtures.

## Handover sequence

1. P011 fixes responsibility and disposition.
2. P012 exposes a bounded CLI process boundary without fallback.
3. P013 proves one read-only structural diagnostic.
4. P014 blocks ownership backflow and retires any temporary exception.

If a later card discovers mixed responsibility or building-specific assumptions
inside the pilot, it must split or reject the slice rather than broaden V4.
