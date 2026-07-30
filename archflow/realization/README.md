# Deterministic sandbox realization

`sandbox.py` evaluates a compiled P047 geometry program without filesystem,
network, CAD, BIM, game, or canonical-state access. The first supported slice
is explicit and bounded: typed axis-aligned solids, polylines, union,
difference, intersection, and caller-supplied immutable mesh assets. Unsupported
operation kinds return a rejected `SandboxRealizationReceipt@1`; they are never
silently approximated.

`HybridSandboxScene@1` retains analytic CSG and complete mesh vertices/faces,
stable object identity, source-object digests, semantic-binding digests, exact
base, and workspace. Coordinate frames are applied deterministically.
Construction operands, hosted opening cuts, and clearance envelopes remain
reference geometry rather than being reintroduced as physical solids.

`DerivedSandboxVoxelView@1` is a bounded validation projection. Its digest binds
the exact scene, realization receipt, resolution policy, occupancy, openings,
walkable regions, support relations, local detail samples, and any declared
sampling loss. Mesh geometry remains in the scene; a finer local sample does
not change the global usability grid. The view supplies a content-addressed
artifact and deterministic `VoxelObservation@1` for P030.

`SandboxRealizationArchiveRecord@1` reloads rejected, repaired, or downstream
accepted disposition labels only when they bind exact realization and decision
receipts. It has no decision or canonical-write authority. Persistence remains
the responsibility of the generic project repository.

The renderer is a separate read-only adapter. No renderer, voxelizer, archive,
or realization receipt performs repair, hard validation, approval, promotion,
or external execution.
