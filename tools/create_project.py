"""Create one external P036 project without running or issuing a design.

    python tools/create_project.py --project D:/work/projects/my-project
    python tools/create_project.py --project D:/work/projects/my-project \
        --state-record D:/inputs/state-record.json --seats-file D:/inputs/seats.json

The directory name is the project id, as required by Studio's existing binding.
Supplied inputs must name that project. They remain authored work in progress;
continuing a retained project instead requires its complete project directory.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from archflow.project.repository import FilesystemProjectRepository, ProjectRepositoryError
from archflow.project.refs import require_identifier
from archflow.state.state_record import StateRecord
from tools.run_project import _seat


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, type=Path, help="new external project directory; its name is the project id")
    parser.add_argument("--state-record", type=Path, help="authored StateRecord@1 JSON for this project")
    parser.add_argument("--seats-file", type=Path, help="authored runner seat pack; requires --state-record")
    args = parser.parse_args(argv)
    try:
        root = args.project.resolve()
        if root.is_relative_to(REPO):
            raise ValueError("choose a project directory outside the source repository")
        require_identifier(root.name, "project_id")
        if root.exists() and (not root.is_dir() or any(root.iterdir())):
            raise ValueError(f"project directory already contains files: {root}")
        if args.seats_file and not args.state_record:
            raise ValueError("--seats-file requires --state-record")
        record = (
            StateRecord.from_dict(json.loads(args.state_record.read_text(encoding="utf-8-sig")))
            if args.state_record
            else StateRecord(project_id=root.name, run_id="authored", entities=())
        )
        if record.project_id != root.name:
            raise ValueError("the state record project_id must match the new directory name")
        if record.base is not None:
            raise ValueError("a retained/bound record cannot initialize a new project; use the complete project copy to continue it")
        seats = None
        if args.seats_file:
            seats = json.loads(args.seats_file.read_text(encoding="utf-8-sig"))
            if not isinstance(seats, dict) or not seats.get("seats"):
                raise ValueError("the seat pack must declare at least one seat")
            if not isinstance(seats.get("commitment_ref"), str) or not seats["commitment_ref"].strip():
                raise ValueError("the seat pack must name its commitment_ref")
            for payload in seats["seats"]:
                _seat(payload)
        repository = FilesystemProjectRepository.initialize(
            root,
            project_id=root.name,
            initial_state={"project_id": root.name, "version": 0},
            authored_record=record.to_dict(),
            seat_pack=seats,
        )
    except (OSError, KeyError, TypeError, ValueError, ProjectRepositoryError) as exc:
        parser.exit(2, f"create_project: {exc}\n")
    print(repository.layout.root)
    print("Created project at version 0; no run or model has been produced.")
    if not record.entities:
        print("The project is empty. Add authored design inputs and seats before running a modeling candidate.")
    elif seats is None:
        print("Supply input/runner/seats.json before running a modeling candidate.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
