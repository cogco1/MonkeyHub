"""Carry one project's drawing recipe to another project: export it as a file, import it on a person's word.

    python tools/export_drawing_recipe.py export --project <project dir> --decision <decisionId> --out <file>
    python tools/export_drawing_recipe.py import --project <project dir> --file <file> [--confirm]

``export`` reads one active project recipe (a person's confirmed ``require``
drawing decision with a recipe binding) and writes it as a
``DrawingRecipeExport@1``: its paper-space values, the target they sit under,
the hold it was given, the decision's id and the sha256 of the exact revision
exported, and the export's own sha256 over all of that. Nothing else of the
project travels - no page, path, run, project id, actor or wording - and one
revision always exports to the same content. It writes nothing into the
project and never overwrites a file.

``import`` reads that file and checks it: its closed form, its values bounded
as a drawing request bounds them, and its content against its sha256, so a
file changed after export is refused. Without ``--confirm`` it shows the one
decision it would retain and writes nothing. ``--confirm`` is the person's
explicit confirmation - an agent never passes it on its own - and retains that
decision: the person's ``require`` in the drawing domain, held as
``soft_preference`` for the whole project and evidenced by the export's
sha256. The project's new drawings then start from it until something
stronger says otherwise; a person who confirms the value on one of the
project's own pages holds it more strongly. A key the project already holds
as a soft_preference is the decisions' own 409 DECISION_RECIPE_CONFLICT.

A runtime may keep the project open. The import goes through the Studio's
own decision function, which holds the project's lock - the HEAD lock a
runtime takes for each decision write - from reading the decisions to
retaining the new one, so the one-value check and the write never interleave
with the runtime's. The runtime reads the new decision on its next request.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

REPO = Path(__file__).resolve().parents[1]
for path in (REPO, REPO / "apps/archflow-studio/api"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from archflow.project.repository import ProjectRepositoryError  # noqa: E402
from archflow_studio_api.application.authentication import ActorAttribution  # noqa: E402
from archflow_studio_api.application.binding import ProjectBinding  # noqa: E402
from archflow_studio_api.application.decisions import (  # noqa: E402
    IMPORTED_RECIPE_STRENGTH,
    DecisionRevision,
    RecipeExport,
    import_recipe,
    read_recipe_export,
    recipe_export,
)
from archflow_studio_api.settings import StudioSettings  # noqa: E402
from archflow_studio_api.transport.errors import StudioError  # noqa: E402

# Who an import is attributed to: this tool's own boundary, local and
# unauthenticated, stated as such - as the Studio states its local boundary -
# rather than borrowed from a person nobody identified. ``--confirm`` is the
# person; this names the surface they used.
ATTRIBUTION = ActorAttribution("tool:export-drawing-recipe", False, "tool")


def _binding(project: Path) -> ProjectBinding:
    return ProjectBinding.open(StudioSettings(project_dir=project.resolve()))


def _values(graphics: Mapping[str, float]) -> str:
    return ", ".join(f"{key} {value:g} mm" for key, value in graphics.items())


def confirmation_words(export: RecipeExport) -> str:
    """What the person is shown and confirms; it is retained as the decision's wording."""

    return f"Import drawing recipe {export.sha256[:12]} as this project's preference: {_values(export.graphics)}."


def _write_export(path: Path, document: Mapping[str, Any]) -> None:
    """The one file this tool writes, created new: an export never replaces anything."""

    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(document, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")


def export(project: Path, decision_id: str, out: Path) -> dict[str, Any]:
    """Write one active recipe decision of ``project`` to ``out`` and return what was written."""

    document = recipe_export(_binding(project), decision_id)
    _write_export(out, document)
    return document


def import_file(project: Path, file: Path, *, confirm: bool) -> tuple[RecipeExport, str, DecisionRevision | None]:
    """Check ``file`` and, only when ``confirm``, retain it in ``project``.

    Returns the checked export, the words the person confirms, and the
    retained decision revision (None when nothing was written).
    """

    document = json.loads(file.read_text(encoding="utf-8-sig"))
    checked = read_recipe_export(document)
    binding = _binding(project)
    words = confirmation_words(checked)
    if not confirm:
        return checked, words, None
    return checked, words, import_recipe(binding, document, raw_language=words, source_kind="human",
                                         attribution=ATTRIBUTION)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    out = commands.add_parser("export", help="write one active project recipe as a DrawingRecipeExport@1 file")
    out.add_argument("--project", required=True, type=Path, help="the project the recipe decision is in")
    out.add_argument("--decision", required=True, help="the recipe decision's decisionId")
    out.add_argument("--out", required=True, type=Path, help="the file to create; an existing file is never replaced")
    into = commands.add_parser("import", help="retain an export as another project's preference")
    into.add_argument("--project", required=True, type=Path, help="the project to import into")
    into.add_argument("--file", required=True, type=Path, help="a DrawingRecipeExport@1 file")
    into.add_argument("--confirm", action="store_true",
                      help="a person confirms the import; without it nothing is written")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # decision ids and words; the console is not always utf-8
    except (AttributeError, ValueError):
        pass
    try:
        if args.command == "export":
            document = export(args.project, args.decision, args.out)
            recipe = document["recipe"]
            print(f"exported decision {args.decision}, revision sha256 {document['source']['revisionSha256']}")
            print(f"  recipe  {recipe['targetRef']} {_values(recipe['graphics'])}, held {recipe['strength']}")
            print(f"  sha256  {document['sha256']}")
            print(f"  file    {args.out}")
            return 0
        checked, words, revision = import_file(args.project, args.file, confirm=args.confirm)
    except StudioError as exc:
        # The decisions' own refusal, with its code: a changed file, a value
        # this project already holds at that hold, or a decision that is not
        # an active recipe.
        print(f"refused ({exc.status} {exc.code}): {exc.detail}", file=sys.stderr)
        if exc.code == "DECISION_RECIPE_CONFLICT":
            print("Nothing was imported: this project keeps one value per key, hold and reach. Revoke or "
                  "supersede that decision in this project first.", file=sys.stderr)
        return 1
    except (ProjectRepositoryError, OSError, ValueError) as exc:
        # An unreadable, existing or non-JSON file, or a project whose lock
        # another process held past the repository's wait, said without a
        # traceback. Nothing was written; running it again is safe.
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(f"export  {checked.sha256}")
    print(f"  recipe  {checked.target_ref} {_values(checked.graphics)}, held {checked.strength} where it was exported")
    print(f"  from    decision {checked.decision_id}, revision sha256 {checked.revision_sha256}")
    print(f"  as      require, {IMPORTED_RECIPE_STRENGTH} for the whole project, sourceKind human")
    print(f"  words   {words}")
    if revision is None:
        print("Nothing was written. Run again with --confirm to retain it as this project's preference.")
        return 0
    print(f"retained decision {revision.decision_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
