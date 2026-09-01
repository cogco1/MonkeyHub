"""Promote or import component templates between P036 projects (P091).

Thin CLI over ``archflow.capabilities.component_library``. The tool owns
no authority and never invents records: it reads one existing
component-template record, applies the two-vote guard (or a recorded
waiver reference the caller supplies), and persists the outcome with
its receipt through the ordinary repository ports.

Usage:
  python tools/run_component_import.py promote
      --source-root <project> --source-record <relative-record-path>
      --library-root <project> --library-run <run-id>
      [--waiver-ref <typed-ref>]
  python tools/run_component_import.py import
      --library-root <project> --library-record <relative-record-path>
      --target-root <project> --target-run <run-id>
      --bind <template-ref>=<local-ref> [--bind ...]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from archflow.capabilities.component_library import (  # noqa: E402
    import_component_template,
    promote_component_template,
)
from archflow.project import (  # noqa: E402
    FilesystemProjectRepository,
    PersistenceArea,
    PersistenceDestination,
)
from archflow.state.component_template import ComponentTemplate  # noqa: E402


def _record_ref(repository: FilesystemProjectRepository, relative: str):
    from archflow.project.refs import ProjectRecordRef

    path = Path(relative)
    digest = path.stem.rsplit("-", 1)[-1]
    return ProjectRecordRef(
        project_id=repository.load_manifest().project_id,
        relative_path=relative.replace("\\", "/"),
        sha256=digest,
        media_type="application/json",
    )


def _load_template(root: Path, relative: str):
    repository = FilesystemProjectRepository.open(root)
    ref = _record_ref(repository, relative)
    template = ComponentTemplate.from_dict(repository.load_json(ref))
    return repository, ref, template


def _destination(run_id: str) -> PersistenceDestination:
    return PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=run_id)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    promote = sub.add_parser("promote")
    promote.add_argument("--source-root", type=Path, required=True)
    promote.add_argument("--source-record", required=True)
    promote.add_argument("--library-root", type=Path, required=True)
    promote.add_argument("--library-run", required=True)
    promote.add_argument("--waiver-ref")

    imp = sub.add_parser("import")
    imp.add_argument("--library-root", type=Path, required=True)
    imp.add_argument("--library-record", required=True)
    imp.add_argument("--target-root", type=Path, required=True)
    imp.add_argument("--target-run", required=True)
    imp.add_argument(
        "--bind",
        action="append",
        default=[],
        help="template-basis-ref=local-record-ref (repeatable)",
    )

    args = parser.parse_args()
    if args.command == "promote":
        _, source_ref, template = _load_template(
            args.source_root, args.source_record
        )
        library = FilesystemProjectRepository.open(args.library_root)
        library_run = library.load_run(args.library_run)
        template_ref, receipt_ref = promote_component_template(
            library,
            library_run=library_run,
            library_destination=_destination(args.library_run),
            template=template,
            source_ref=source_ref,
            waiver_ref=args.waiver_ref,
        )
        print(json.dumps({
            "template_ref": template_ref.uri,
            "promotion_receipt_ref": receipt_ref.uri,
        }, indent=1))
        return 0

    _, library_ref, template = _load_template(
        args.library_root, args.library_record
    )
    bindings = {}
    for row in args.bind:
        if "=" not in row:
            parser.error(f"--bind needs template-ref=local-ref, got {row!r}")
        key, value = row.split("=", 1)
        bindings[key] = value
    target = FilesystemProjectRepository.open(args.target_root)
    target_run = target.load_run(args.target_run)
    template_ref, receipt_ref = import_component_template(
        target,
        target_run=target_run,
        target_destination=_destination(args.target_run),
        template=template,
        library_ref=library_ref,
        evidence_rebinding=bindings,
    )
    print(json.dumps({
        "template_ref": template_ref.uri,
        "import_receipt_ref": receipt_ref.uri,
    }, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
