# GH-252

Issue: https://github.com/cogco1/MonkeyHub/issues/252
Base: `1e38914c` (batch I, after batch H2).

A project's confirmed drawing recipe can travel: exported as a versioned file with its evidence digest and imported by a second project as a person-confirmed soft preference, so one project's accepted correction becomes another project's default.

Batch I (2026-09-25/26), in the owner's order; plans are kept outside the repo.

## Lane `recipe-export`

- Landed (first #252 slice, the "project recipe out/in" of the 07 plan): a project recipe travels as a file. `tools/export_drawing_recipe.py export` writes one active recipe decision as `DrawingRecipeExport@1`: its values, target and hold, the decision id, the sha256 of the revision exported and its own canonical sha256, and nothing else of the project. `import` checks the file (closed form, a cut-plan request's bounds, content against its sha256), shows the decision it would retain, and only with `--confirm` retains it in the other project: a person's `require`, `soft_preference` for the whole project, citing the new source `{kind: recipe-export, exportSha256}`; a key already held there at that hold is the existing 409. The import goes through `import_recipe` under the project's HEAD lock, so a runtime may keep the project open. Two OCCT-backed projects under their own ids show a hatch correction confirmed in the first starting the second's next new drawing.
- Open: the generated Studio client does not yet name the new source kind (regenerated at integration). An import cannot supersede a value the project already holds; that decision is revoked first. In the importing project the imported preference covers its key for correction suggestions (`drawing_corrections` reads `project_recipe` at any hold), so corrections repeated away from it are not offered as that project's own recipe; whether a preference should hold back that offer is the owner's call. The export is a local file, not yet a versioned toolbox entry or a team/firm layer, and no Drawing or Hub surface exports or imports.
