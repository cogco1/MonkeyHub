"""Deterministic package validation and host-surface export for P044."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from archflow.adapters.skill_surfaces import (  # noqa: E402
    codex_skill_file,
    render_skill_surface,
)
from archflow.skills.contracts import canonical_json  # noqa: E402
from archflow.skills.package import (  # noqa: E402
    MANIFEST_NAME,
    load_skill_package,
    load_skill_packages,
    sealed_manifest_payload,
)


def _absolute_directory(value: str, field: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        raise ValueError(f"{field} must be an explicit absolute path")
    return path


def _replace_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
        text=True,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _seal(args: argparse.Namespace) -> int:
    root = _absolute_directory(args.root, "root")
    payload = sealed_manifest_payload(root)
    formatted = json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
    ) + "\n"
    _replace_text(root / MANIFEST_NAME, formatted)
    package = load_skill_package(root)
    print(
        canonical_json(
            {
                "skill_id": package.spec.skill_id,
                "version": package.spec.version,
                "package_digest": package.spec.package_digest,
            }
        )
    )
    return 0


def _validate(args: argparse.Namespace) -> int:
    roots = tuple(_absolute_directory(value, "root") for value in args.roots)
    packages = load_skill_packages(roots)
    print(
        canonical_json(
            {
                "schema": "SkillValidation@1",
                "packages": [
                    {
                        "skill_id": item.spec.skill_id,
                        "version": item.spec.version,
                        "package_digest": item.spec.package_digest,
                    }
                    for item in packages
                ],
            }
        )
    )
    return 0


def _export(args: argparse.Namespace) -> int:
    root = _absolute_directory(args.root, "root")
    destination = _absolute_directory(args.output, "output")
    package = load_skill_package(root)
    artifact = render_skill_surface(package, args.surface)
    if args.surface == "codex":
        codex_skill_file(artifact)
    destination_resolved = destination.resolve()
    targets: list[tuple[Path, bytes]] = []
    for item in artifact.files:
        target = destination.joinpath(*Path(item.relative_path).parts)
        resolved_target = target.resolve()
        if not resolved_target.is_relative_to(destination_resolved):
            raise ValueError("generated surface path escaped output root")
        if target.exists():
            raise FileExistsError(
                f"refusing to overwrite generated surface file: {target}"
            )
        targets.append((target, item.content))
    for target, content in targets:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    print(canonical_json(artifact.to_dict()))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="skillctl.py")
    commands = parser.add_subparsers(dest="command", required=True)

    seal = commands.add_parser("seal")
    seal.add_argument("--root", required=True)
    seal.set_defaults(handler=_seal)

    validate = commands.add_parser("validate")
    validate.add_argument("roots", nargs="+")
    validate.set_defaults(handler=_validate)

    export = commands.add_parser("export")
    export.add_argument("--root", required=True)
    export.add_argument(
        "--surface",
        choices=("codex", "claude", "kimi"),
        required=True,
    )
    export.add_argument("--output", required=True)
    export.set_defaults(handler=_export)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
