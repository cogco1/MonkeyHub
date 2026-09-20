# Progressive structural analysis — GH-125

This runnable experiment takes the **same six geometry entities** through explicit
role, semantic material and physical enrichment. Five selected members then compile
to a solver-neutral projection and run in **PyNiteFEA 3.2.0**. A separate closed-form
solution checks the actual displacements, reactions, shear and moments. No private
project or engineering document is used.

The four milestones below are this fixture's **commitment profiles**, corresponding
to the progression proposed in [#125](https://github.com/cogco1/MonkeyHub/issues/125).
They are not four entity schemas, accepted `DesignStage` nodes, or a new global
Stage mechanism. The experiment leaves both issue `HEAD` and accepted design history
unchanged. It demonstrates the semantic/physical interface; it does not accept a
project, certify a concrete design, or add a Hub UI.

## Run and replay

Use Python 3.12 from the repository root. Install only in an explicit external venv
(replace the example external paths with your own empty locations):

```powershell
python -m venv "$env:TEMP\gh125-venv"
& "$env:TEMP\gh125-venv\Scripts\python.exe" -m pip install -r labs/structural_analysis/requirements.txt
& "$env:TEMP\gh125-venv\Scripts\python.exe" -m unittest labs.structural_analysis.test_readiness labs.structural_analysis.test_solver
& "$env:TEMP\gh125-venv\Scripts\python.exe" -m labs.structural_analysis.demo --project-dir "$env:TEMP\gh125-project" | Set-Content -Encoding utf8 "$env:TEMP\gh125-report.json"
& "$env:TEMP\gh125-venv\Scripts\python.exe" -m labs.structural_analysis.demo --project-dir "$env:TEMP\gh125-project" --replay-report "$env:TEMP\gh125-report.json"
```

The first demo command refuses an existing project directory. It creates an external
P036 project, retains seven actual revisions and returns JSON with exact record and
artifact refs plus measured results. Preserve that stdout report for replay: these
lab artifacts are **not registered Hub documents/models**, and project archive/restore
integration is not claimed. All project writes use `FilesystemProjectRepository`;
the report is an external experiment output, never a second project-state store.
Replaying in a fresh Python process verifies each immutable record, its run manifest,
the exact-base operator, geometry operations, readiness, projection and solver result.
Replay compares the pinned environment's results exactly; a different backend or
numeric environment is reported as a mismatch, not silently accepted as equivalent.

Readiness tests use no third-party solver. Solver tests require the pinned package
and execute it; they do not skip a missing backend. Calling the demo without it still
retains input/projection evidence, but reports `unavailable`, never a solved result.

## One state and one mutation path

`fixture.py` authors ordinary `Element@1` rows realized by `prism` and
`planar-surface`. `building` uses the existing `role.whole`; member meaning can remain
unknown. `enrich` and the sensitivity/revocation helpers compile the existing
`compile_component_edit`; only `apply_state_record_operator` derives successors.
`bound_to` supplies the existing P036 run/base binding. Existing parameter locks and
protected closures continue to apply.

| Milestone | Same five selected members acquire | Structural readiness |
| --- | --- | --- |
| Geometry | `semantic.status = unknown`; geometry and provenance only | unavailable |
| Roles | Existing `role.structural_support` / `role.load_transfer`, attributed as hypotheses | unavailable |
| Material | Semantic `material_ref = demo-reinforced-concrete`; no inferred mechanics | unavailable |
| Physical | Sourced physical profile, section, idealization, connectivity, releases, supports, loads and policy | ready for this capability |

The optional `semantic`, `material` and `structural` mappings live on the existing
entity's `fields`, not in a new shared schema. A profile is local sourced property data,
with an explicit profile reference and semantic-material reference, not a material
database or a second project library. No production state/DTO/module contract changes.
The physical revision introduces substantial facts while `roof`, the building and
the original readings remain byte-for-byte reusable. Producer choice stays `prism`.

The roof remains unknown and is excluded from the selected structural subset, even
at the last milestone. `commitment_review` distinguishes `not_required` from unknown
facts and hypotheses. A retained, analytically located overlap observation between
the centre column and left beam progresses from informational to unresolved, warning,
and blocker under this **fixture-only policy**. That is not a CAD collision inspection.
It never repairs the joint or invalidates the identity. Analysis readiness is a separate
question: this explicitly idealized centreline model can run while detailed geometry
coordination remains blocked. It cannot override the real parameter locks.

Revoking `beam-0`'s structural interpretation produces another normal operator/run.
Readiness becomes unavailable, while the previously attributed role and physical
results remain reproducible from their retained versions.

## Capability, projection and evidence

`analysis.py` checks only the explicitly selected entities. It requires a registered
load-bearing role, semantic material identity, an explicitly matching physical
profile, E and Poisson ratio, A/Iy/Iz/J, endpoint coordinates/joints, release assumptions,
support/load declarations and the supported analysis policy. It checks units,
sources, status, ranges, finite positive stiffness/section quantities, joint
consistency and load positions. It converts supported units without changing the
authored properties. Density is preserved and validated when supplied; it is not
required for this no-self-weight static case. Strength and thermal expansion are not
needed. `concrete` and rendering/PBR metadata never supply numerical values.

| Outcome | Meaning |
| --- | --- |
| `unavailable` | A prerequisite is absent or explicitly unknown, or the optional backend is absent |
| `invalid` | Supplied input is inconsistent, nonphysical for this model, or bound to the wrong revision |
| `unsupported` | Supplied unit/idealization/policy/backend version is outside this bounded consumer |
| `failed` | The actual backend ran and failed, e.g. a singular stiffness matrix despite supplied supports |
| `solved` | The stated linear model returned finite numerical results; no structural-design approval |

`AnalysisSpec` is a plain transient value, not a persisted schema family. It contains
derived nodes/members with source entity ids, SI quantities, original sourced facts,
explicit policy and the exact project/run/base/content binding. It is compiled and
immediately consumed within this trusted lab call; it is not an externally submitted
or authorized engineering payload. Nodes sharing a declared joint must agree exactly;
the compiler neither merges by proximity nor snaps architectural geometry.

`solver.py` receives only that projection and returns evidence. It has no StateRecord,
repository, geometry mutation or acceptance writer. Results carry exact source binding,
full member/profile provenance, solver and numeric-library versions, nodal displacement
and rotation, support reaction and moment, and member force/displacement samples.
`demo.py` retains the projection/result as a JSON artifact through P036 `ingest` and
reads its exact bytes through P036's hash-verified JSON reader. It never writes solver
results onto canonical entities. Exact artifact refs are retained by the external
stdout report; no new record kind, persistence owner or receipt hierarchy is added.

## Actual model and independent check

The synthetic pavilion section has three 3 m columns at x = 0, 3, 6 m, and two 3 m
beams at y = 3 m; y is up, z is out of plane. A retained roof surface is not a shell
element. Two explicit 10 kN downward midpoint point loads stand in for gravity;
roof load distribution is **assumed, not calculated**. Column bases fix six DOFs;
column tops restrain DZ/RX/RY. Beam ends release local Rz. No self-weight is added.

All values below cite `fixture:gh-125-synthetic-assumptions`, carry units and
`hypothesis` status. They are selected demonstration values, not concrete values
deduced from a name, specified material test data or a code table.

| Input | Value |
| --- | --- |
| E | 30 GPa; supplied hypothesis range 27–33 GPa; uncracked, linear isotropic |
| Poisson ratio | 0.2, dimensionless |
| Density | 2400 kg/m³, retained but unused in this load case |
| Column section | 0.3 × 0.3 m; A = 0.09 m², Iy = Iz = 0.000675 m⁴ |
| Beam section | 0.2 × 0.4 m; A = 0.08 m², Iy = 0.000266666667 m⁴, Iz = 0.001066666667 m⁴ |
| J | Explicit synthetic 0.0001 m⁴; no torsion in this case |

The adapter derives isotropic `G = E/[2(1+nu)]`. PyNite's material `rho` means weight
density; its unused adapter value is zero only because self-weight is explicitly
disabled. This does not replace or reinterpret the retained mass density.

Independent equilibrium gives each beam's end reaction P/2 and midpoint moment
magnitude PL/4. Each column shortens by NL/(EA). Integrating the elastic beam equation
with pin-end conditions gives midpoint displacement `-PL³/(48EI)` relative to the
support chord; add the two column settlements' average for absolute displacement.
`hand_solution` refuses a different topology, release pattern, loading or support set;
it is a closed-form check, never a general solver or failure fallback.

Actual isolated run on 2026-09-20: Python 3.12, PyNiteFEA 3.2.0, NumPy 2.5.3,
SciPy 1.18.1. Each case was computed by PyNite and independently checked:

| Quantity | Baseline | E halved on all members | Beam Iz doubled only |
| --- | ---: | ---: | ---: |
| Either beam absolute midpoint DY, mm | -0.184114583333 | -0.368229166667 | -0.096223958333 |
| Either beam relative midpoint DY, mm | -0.17578125 | -0.3515625 | -0.087890625 |
| Left/right column top DY, mm | -0.005555555556 | -0.011111111111 | -0.005555555556 |
| Centre column top DY, mm | -0.011111111111 | -0.022222222222 | -0.011111111111 |
| Base vertical reactions, kN | 5 / 10 / 5 | 5 / 10 / 5 | 5 / 10 / 5 |
| Either beam midpoint local Mz, kN·m | -7.5 | -7.5 | -7.5 |

Tests use relative tolerance 1e-10 with absolute displacement tolerance 1e-12 m;
nominally zero end moments use 1e-8 N·m to accommodate floating-point cancellation.
The observed baseline residual end moment was 3.64e-12 N·m. The section case is an
explicit **effective-inertia sensitivity**, not an assertion that an unchanged
concrete member has physically doubled its gross section. It does not resize protected
geometry. If a design response requires geometric changes, the existing operator
must propose them and existing locks may refuse them; analysis grants no override.

The singular-support test also actually calls PyNite: supplying only out-of-plane
restraints leaves rigid-body motion and returns `failed`, without fabricated results.

## Research and implementation choices

- Turner, Clough, Martin & Topp (1956), *Stiffness and Deflection Analysis of Complex
  Structures*, DOI [10.2514/8.3664](https://doi.org/10.2514/8.3664), printed p.807 =
  PDF p.4 of the [university-hosted original paper](https://www.ce.memphis.edu/7117/notes/presentations/papers/Turner%20et%20al%20(1956)%20Stiffness%20and%20deflection%20analysis%20of%20comlex%20strucutres.pdf).
  Its equilibrium, compatibility and constitutive relations support requiring explicit
  connectivity, boundaries and physical parameters before assembling F = Kd. It does
  not justify inferring those facts from architectural geometry or material labels.
- [MIT 1.050, Fall 2004, Problem Set 11](https://ocw.mit.edu/courses/1-050-solid-mechanics-fall-2004/fd4eff39aec922b8c07660006f40686e_pset04_11.pdf),
  PDF p.2, provides the simply-supported point-load relation used at b = x = L/2.
  This independent mechanics check complements solver regression evidence.
- [PyNiteFEA 3.2.0](https://pypi.org/project/PyNiteFEA/3.2.0/) is pinned rather than
  vendored. We inspected the [release commit](https://github.com/JWock82/Pynite/tree/601eabc8c2e0d861e3dcede2e99c00e6a2793ab4)
  and [Member3D implementation](https://github.com/JWock82/Pynite/blob/601eabc8c2e0d861e3dcede2e99c00e6a2793ab4/Pynite/Member3D.py):
  elastic beam-column stiffness and static condensation of end releases directly
  match this fixture. The installed wheel's FEModel3D, Member3D, PhysMember and BeamSegZ
  files were checked against that commit. Wheel SHA-256:
  `3e2d0e8e294ac1b328c756349131596b39c4c4dfce3663ec3b53cbf16f6aa99b`.
  Its [MIT license](https://github.com/JWock82/Pynite/blob/601eabc8c2e0d861e3dcede2e99c00e6a2793ab4/LICENSE)
  permits reuse; the dependency retains its own copyright/license. No third-party
  solver source is copied into this repository.
- We adopt `FEModel3D.add_material/add_section/add_member`, explicit support/release
  methods, `add_member_pt_load`, and `analyze_linear`; results use `deflection`,
  `rel_deflection`, `shear`, `moment` and node reaction/displacement APIs. A mock adapter
  is unnecessary because this real backend already covers the requested bounded case.
  A custom stiffness solver or nonlinear concrete implementation would add unneeded
  scope without strengthening the progressive-state claim. The
  [official member documentation](https://pynite.readthedocs.io/en/latest/member.html)
  describes the formulation and limits. This run excludes shear deformation,
  P–Delta, buckling, cracking, creep, reinforcement design and code compliance.

## Acceptance mapping

| Issue acceptance, including both clarifications | Concrete evidence |
| --- | --- |
| Geometry exists before classification; identity/producer persists | `test_readiness`: initial unknowns, all entity ids and real producer operation equality through four milestones |
| Semantic material and sourced physical profile are separate | `fixture.enrich`; material-only revision is unavailable; every demo physical quantity carries unit/source/status |
| Capability-specific subset and explicit missing prerequisites | `test_readiness`: roof excluded; missing profile, section, endpoints, supports, loads, releases and E; unsupported units and nonphysical values |
| Solver-neutral projection → real backend → exact entity/revision/profile evidence | `analysis.compile_analysis`, `solver.solve`, `test_solver` numerical and source assertions |
| Save/reopen and historical representations | Fresh-process replay of seven P036 revisions, retained exact-base operators, geometry, projections and solver results |
| E/section revision affects physics without design drift | Three real solves and closed-form comparisons; identical six geometry operation sets and entity ids |
| Protected earlier geometry cannot silently change | Existing lock-value, binding-detachment and protected-entity refusals in `test_readiness`; solver has no writer |
| Unknown/assumed/not-required/failure are distinct | Readiness findings, source statuses, fixture commitment policy and actual singular-support run |
| Early overlap retained; later policy changes consequence | Same `Reading@1` observation across all profiles; no repair or identity replacement |
| Attributed facets can be withdrawn without erasing history | `withdraw-role` operator plus fresh-process reproduction of earlier role/profile/results |
| Different projections retain one identity | Existing geometry producer operations and derived FE members both map to the same `Element@1` ids; no BIM exporter is claimed |

All of the issue's bounded framework-demo checks are executable here. Production Hub
integration, engineering acceptance, and a full Stage ontology remain outside this
example; four retained revisions are not presented as accepted product Stages.
