# W1-B — stair, wedge, shell: report

## Status

DONE_WITH_CONCERNS

Three producers added to `archflow/capabilities/element_producers.py` and registered in `PRODUCERS`
(`stair`, `wedge`, `shell`), with tests in `tests/test_element_producers.py`. Two things depart from a
literal reading of the brief and are stated under **Concerns**: the axis-point reference form the brief
quoted does not exist in that shape, and the shared `_loft` helper gained one optional argument so a
wedge or a dome shell can seat with a base offset instead of silently dropping it.

## Commits

- `ad93f3a` — Three producers the tree lacked: a flight of steps, a sloped-top wedge, a hollow shell
  (`archflow/capabilities/element_producers.py`, `tests/test_element_producers.py`)
- `HEAD` after this file — this report (`.superpowers/sdd/reports/2026-09-04-w1-b-producers.md`), a
  second commit only so it could name the hash of the first.

Branch `worktree-agent-ab8035c11e7c3f5e1`, based on `6a4a31a`. Nothing merged, rebased or pushed.

## Tests

```
cd D:\ARCHFLOW_V4\.claude\worktrees\agent-ab8035c11e7c3f5e1
py -3.12 -m unittest tests.test_element_producers
Ran 15 tests in 0.074s
OK

py -3.12 -m unittest discover -s tests -t .
Ran 349 tests in 18.949s
OK

py -3.12 tools/archcheck.py
ARCHITECTURE PASS (183 files, 1.660s)
```

Ten tests existed in the two files before (5 in `tests.test_element_producers`); ten new tests were
added, all of them at producer level plus one ordering test.

Beyond the unittests I compiled the three producers' output through `compile_geometry_program` and
`translate_to_rhino_python` once by hand (not committed) to confirm the ops are well formed on the
spine: `expected_object_bounds` gives `obj-stair-north-9` a top of `5.37`, exactly the published
`stair-north-top`; `obj-abutment-north` is `[0, 0, -14.85] .. [4, 2.5, -12.85]`; the dome shell is
`[-5, 0, -18.85] .. [5, 3, -8.85]`; the CAD translation reports `losses: ()`.

## Producer vocabulary (for the re-index task)

Every producer takes `references["base"]`, which is the file's usual elevation reference:
`{"level": "<level-id>"}`, `{"offset_from": {"level": ..., "offset": m}}`, or
`{"datum": "<element>-top"}` (optionally with `"offset"`). Every plan reference goes through
`parse_reference` / `resolve_plan`, so its forms are `{"grid": "<role>"}`, `{"grid": [roleA, roleB]}`,
`{"axis_point": {"axis": "<role>", "along": m}}`, `{"host": {"element": "<id>", "along": m,
"across": m}}`. All three publish `<element_id>-top` and declare
`ProducedRelation("<id>-stands-on", "support", <base datum>, <id>, <base datum>,
_seat_parameters(base_offset))`.

### `stair`

```
references: {"from": <plan ref>, "to": <plan ref>, "base": <elevation ref>}
params:     {"count": int >= 1,
             "rise": m > 0,
             "going": m > 0            (optional; default |to - from| / count)
             "width": m > 0,
             "thickness": m >= 0}      (optional; 0 or absent = solid block steps)
```

- ops: `<id>-0` .. `<id>-(count-1)`, each an EXTRUSION, output `obj-<id>-<k>`.
- step `k` is a box `going` long along the from→to line (interval `[k·going, (k+1)·going]` from
  `from`) and `width` across it, centred on the line; its `base_offset` is `base_offset + k·rise`
  and its extrusion vector is `rise` for solid steps, `thickness` for slab steps.
- publishes `<id>-top` at `base + base_offset + count·rise`, `published_by` `obj-<id>-0`.
- refuses: a missing `from`/`to`/`base`; `count < 1`; a non-positive `rise`/`width`/`going`; a
  negative `thickness`; a zero-length line; a declared `going` whose `count·going` misses the
  resolved line length by more than 1 mm.

### `wedge`

```
references: {"from": <plan ref>, "to": <plan ref>, "base": <elevation ref>}
params:     {"depth": m > 0,
             "low": m >= 0,
             "high": m > low,
             "slope_across": bool,     (optional; default false)
             "loft_type": "straight"|"normal"}   (optional, inherited from _loft; default "straight")
```

- one LOFT op, id `<id>`, output `obj-<id>`, `profile_size = 4`, 8 profile points.
- the box `from`→`to`, `depth` across the line and centred on it; each end face is a rectangle
  `[p−n·depth/2 @ 0, p+n·depth/2 @ 0, p+n·depth/2 @ y_plus, p−n·depth/2 @ y_minus]` with
  `n = (uz, −ux)`. Default: near face `(low, low)`, far face `(high, high)` — the top plane rises
  along the length. With `slope_across: true`: both faces `(low at −n, high at +n)`.
- publishes `<id>-top` at `base + base_offset + high`.
- refuses: a missing reference; a non-positive `depth`/`high`; `low < 0`; `high <= low`; a
  zero-length line.

### `shell`

```
references: {"at": <plan ref>, "base": <elevation ref>}
params:     {"outer_radius": m > 0,
             "thickness": 0 < m < outer_radius,
             "height": m > 0,
             "kind": "cylinder" | "dome",   (required, no default)
             "segments": int >= 3,          (optional; default 24)
             "rings": int >= 2,             (optional; default 8; dome only)
             "loft_type": "straight"|"normal"}  (optional, dome only; default "straight")
```

- `cylinder`: one EXTRUSION, id `<id>`, output `obj-<id>`, profile = one closed annulus of
  `2·segments` points (outer circle forward, inner circle back), extruded by `height`.
- `dome`: one LOFT, id `<id>`, output `obj-<id>`, `profile_size = 2·segments`, `rings` annuli.
  Ring `i` sits at `y = height·sin(φ)` with `φ = (π/2)·i/(rings−1)`, outer radius
  `max(outer_radius·cos(φ), thickness/2)` and inner radius `outer · (outer_radius − thickness) /
  outer_radius`.
- publishes `<id>-top` at `base + base_offset + height` for both kinds.
- refuses: a missing `at`/`base`; a non-positive `outer_radius`/`thickness`/`height`;
  `thickness >= outer_radius`; `segments < 3`; a `kind` other than the two; `rings < 2` for a dome.

## Registrations needed

Registry file `governance/module_registry.json`, module `capabilities.element_producers` — I did not
edit it (rule 3). One `owns` line changes:

- replace

  `"the producer dispatch table PRODUCERS: column-array, capitals, beam, pediment, wall, prism, ring, loft, dome-cap, declined"`

  with

  `"the producer dispatch table PRODUCERS: column-array, capitals, beam, pediment, wall, prism, ring, loft, dome-cap, stair, wedge, shell, declined"`

Optional, and only if the controller wants the symbol a test now imports declared: add
`"production_order"` to that module's `public_api` (it is already described in `owns` as "topological
production order"; `tests/test_element_producers.py` imports it directly for the ordering test).
`archcheck` passes without this change.

No new record kind, no new module, no `archflow/project/record_kinds.py` change.

## Concerns

1. **The brief's axis-point reference form does not exist.** The brief writes axis points as
   `{"axis": role, "along": t}`; `parse_reference` only accepts one-key objects, so the real form is
   `{"axis_point": {"axis": role, "along": t}}`. I used the real form in the tests and changed nothing
   in the resolver. The re-index task must emit `axis_point`.

2. **`_loft` gained one optional argument.** The brief asks the wedge to be built "using the file's
   `_loft` helper", but `_loft` emitted no `base_offset`, so a wedge or dome shell whose base is an
   `offset_from` level or an offset datum would have had its offset silently dropped — an unexplained
   epsilon of exactly the kind this module refuses. I added `base_offset: float = 0.0` to `_loft`; it
   appends the same `base_offset` NUMBER parameter `_extrusion` already appends, and only when
   non-zero. Its support relation now also carries `_seat_parameters(base_offset)`, which for the two
   existing callers (`produce_loft`, `produce_dome_cap`, both of which already refuse a non-zero base
   offset) is the empty dict it produced before. No existing behaviour changes; the 349-test suite is
   unchanged and green.

3. **A wedge with `low = 0` emits a degenerate near face.** The brief permits `high > low >= 0`. At
   `low = 0` the near end face's four points collapse onto one line (two coincident pairs), which is
   the correct shape — a knife edge — but a polyline with repeated points. `expected_object_bounds`
   and the translator accept it; Rhino's `AddPolyline` may or may not. I did not refuse it because the
   brief explicitly allows it, and I did not special-case a 3-point profile because `profile_size` must
   be uniform across a loft. If a real abutment ever runs to a knife edge, that is the case to test on
   the CAD side first.

4. **The dome shell is a scaled shell, not a constant-thickness one.** "The inner cap over
   `outer_radius − thickness`" leaves the inner cap's vertical semi-axis unstated. I used the same
   `height` for both caps, so the inner surface is the outer one scaled by
   `(outer_radius − thickness)/outer_radius`: the wall holds `thickness` radially at the springing and
   thins towards the crown. The alternative — an inner cap of height `height − thickness` — drives the
   inner radius to zero below the outer crown and turns the annulus into a disc, which a fixed
   `2·segments` profile cannot express. Both this and the `thickness/2` crown clamp are written into
   the producer's docstring as stated approximations. If the intent was constant normal thickness, the
   dome branch needs a different construction (two lofts and a boolean difference), not a parameter.

5. **An annulus is one self-touching closed polyline.** The cylinder profile and every dome ring go out
   along the outer circle and back along the inner one, exactly as `produce_ring` already builds its
   sectors, so the hole is expressed by the profile rather than by a boolean. This is the file's
   existing convention, inherited deliberately, not a new one.

## Left out and why

- **`governance/module_registry.json`** — not edited; the one `owns` line it needs is under
  *Registrations needed* per common-brief rule 3.
- **The re-index mapping** — the brief says the re-index tool maps the 78 row-less identities onto
  these producers "in a later task". I added no mapping, touched no `element_reindex.py`, and produced
  no Villa data.
- **A CAD-execution / Rhino equivalence witness** for the three new shapes — out of scope here, and the
  `low = 0` and annulus-polyline caveats above are what such a witness would settle.
- **`HostLine` on the stair** — a flight has an obvious reference line and hosting things along it
  (a balustrade, a nosing) would be natural, but neither the brief nor any consumer asked for it, so
  the stair returns `host_line=None` like every producer except `beam` and `wall`.
