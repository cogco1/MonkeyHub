# P105 — The producers the monuments are written in

**Status:** ready (not started)
**Lane:** productization and componentization
**Depends on:** P100 (references and derivations), P102 (the State Record)
**Retires:** the classical-order and solid-of-revolution vocabulary as *operation kinds invented inside a
per-building tool*. After this card that vocabulary exists once, as producers reading references, and the
per-building emitters become its callers until P106 removes them.

## Why

P089 delivered a record-driven runner and proved it on two buildings. It cannot drive the two monuments, and the
reason is not orchestration: their geometry is written in a vocabulary the canonical producers do not have.

Measured on the retained records:

| Project | Retained program | Operation kinds |
|---|---|---|
| Parthenon, stage 4 | 687 operations | 23 kinds, 20 of them classical-order: `doric_shaft`, `doric_echinus`, `doric_abacus`, `doric_neck`, `triglyph`, `ionic_column`, `eave_geison`, `eave_sima`, `pediment_raking_geison`, `pediment_raking_sima`, `pediment_horizontal_geison`, `pediment_tympanum`, `entablature_layer`, `bearing_block`, `acroterion_seat`, `marble_cover_tile_field`, `marble_pan_tile_field`, `marble_eave_terminal`, `marble_ridge_terminal`, `timber_rafter_field`, `timber_bearing_beam`, `timber_ridge_beam` |
| Pantheon | 14 operations | `solid`, `revolve`, `boolean_difference`, `loft` |
| Canonical producers today | — | `column-array`, `capitals`, `beam`, `pediment`, `wall`, `prism`, `ring`, `loft`, `dome-cap`, `declined` |

Flattening 687 semantic operations into anonymous prisms would discard the thing that made those records worth
keeping. The vocabulary is the content.

## Mechanism

1. **Solids of revolution first** (the pantheon's need): a `revolve` producer taking a profile and an axis by
   reference, and the `solid` / `boolean_difference` chain the dome and drum are cut from. Smallest useful slice,
   and it lets one monument replay before the larger vocabulary lands.
2. **The Doric order as a family** (`doric_shaft`, `neck`, `echinus`, `abacus`, `triglyph`): one producer per
   member, placed on grid references, dimensioned by a derivation table in module ratios rather than by literals,
   publishing the datum the member above binds. The order's proportional system is exactly what a
   `DerivationTable@1` is for.
3. **Roof and entablature fields** (`marble_*_field`, `timber_*`, `eave_*`, `pediment_*`): repeating members over a
   span, which is an array over a reference line, not 104 authored coordinates.
4. **The Ionic column** shares the shaft and abacus with the Doric family and differs in its capital; it should
   reuse, not duplicate.
5. **Equivalence per member family**, not per building: each producer reproduces the retained operations of its own
   kind within tolerance before the next one starts.

## Acceptance

- [ ] `revolve`, `solid` and `boolean_difference` producers read references and carry no authored coordinates;
      the pantheon's 14 retained operations are reproduced within 1 mm.
- [ ] The Doric member family is produced from a derivation table in module ratios, and each of the parthenon's
      104-member kinds is reproduced within 1 mm from grid references.
- [ ] Roof and entablature fields are arrays over reference lines, not authored per member.
- [ ] Every producer publishes the datum its neighbour binds; no unexplained epsilon, engagements declared.
- [ ] Full unittest suite and the architecture firewall pass.

## Do-not-do

No new operation kinds in the CAD contract to make a producer easier: a producer emits the existing neutral
operations. No per-building constants inside a producer. No flattening a semantic member into a generic prism to
make the equivalence pass.
