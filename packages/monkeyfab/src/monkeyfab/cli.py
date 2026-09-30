"""File input and output for the first 3D-print preparation workflow."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import math
import os
from pathlib import Path
import sys

import trimesh

from .geometry import prepare_mesh
from .profiles import PROFILES, get_profile
from .send import send_file


UNIT_TO_MM = {"mm": 1.0, "cm": 10.0, "m": 1000.0, "in": 25.4}


def parse_scale(value: str) -> float:
    """Accept a model:source ratio (1:100) or a uniform multiplier (0.01)."""
    try:
        if ":" in value:
            numerator, denominator = value.split(":")
            first, second = float(numerator), float(denominator)
            if not (math.isfinite(first) and math.isfinite(second)) or min(first, second) <= 0:
                raise ValueError
            factor = first / second
        else:
            factor = float(value)
        if not math.isfinite(factor) or factor <= 0:
            raise ValueError
        return factor
    except (ValueError, ZeroDivisionError) as exc:
        raise argparse.ArgumentTypeError("scale must be positive, e.g. 1:100 or 0.01") from exc


def _prepare(args: argparse.Namespace) -> dict:
    source = args.source.resolve()
    if source.suffix.lower() not in {".stl", ".obj"}:
        raise ValueError("this version accepts closed STL or OBJ meshes; export CAD solids to a closed mesh first")
    if not source.is_file():
        raise ValueError(f"input file does not exist: {source}")
    output = args.output.resolve()
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError(f"output must be a new or empty directory: {output}")

    profile = get_profile(args.printer)
    working_volume = profile.working_volume(args.xy_margin, args.z_clearance)
    try:
        mesh = trimesh.load_mesh(source, process=True, skip_materials=True)
    except IndexError as exc:
        raise ValueError(f"input mesh contains an invalid vertex reference: {source.name}") from exc
    scale_factor = args.scale * UNIT_TO_MM[args.input_unit]
    parts = prepare_mesh(mesh, scale_factor, working_volume)

    # The placement table is used to assemble the printed pieces in source coordinates.
    # It is not a new project store or a machine job.
    result = {
        "source_file": source.name,
        "source_unit": args.input_unit,
        "scale": args.scale,
        "source_coordinate_to_mm": scale_factor,
        "output_unit": "mm",
        "printer": asdict(profile),
        "xy_margin_mm": args.xy_margin,
        "z_clearance_mm": args.z_clearance,
        "working_volume_mm": working_volume,
        "suggested_bed_origin_mm": [
            profile.usable_origin_mm[0] + args.xy_margin,
            profile.usable_origin_mm[1] + args.xy_margin,
            0.0,
        ],
        "scaled_source_bounds_mm": (mesh.bounds * scale_factor).tolist(),
        "assembly_rule": "scaled source position in mm = part STL position in mm + assembly_offset_mm",
        "parts": [],
    }

    # Compute and validate the entire split before any result files are written.
    output.mkdir(parents=True, exist_ok=True)
    for number, part in enumerate(parts, start=1):
        filename = f"part_{number:03d}.stl"
        part.mesh.export(output / filename, file_type="stl")
        result["parts"].append({
            "file": filename,
            "grid_index": [int(value) for value in part.grid_index],
            "size_mm": part.mesh.extents.tolist(),
            "assembly_offset_mm": [float(value) for value in part.assembly_offset_mm],
            "volume_mm3": float(part.mesh.volume),
        })
    (output / "parts.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    return result


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="monkeyfab", description="Prepare printable mesh parts and upload sliced Bambu jobs")
    sub = root.add_subparsers(dest="command", required=True)
    profiles = sub.add_parser("profiles", help="show printer envelopes, limits and official sources")
    profiles.add_argument("--json", action="store_true", help="print the full parameter table as JSON")

    prepare = sub.add_parser("prepare", help="scale a closed STL/OBJ and split it into bed-sized parts")
    prepare.add_argument("source", type=Path)
    prepare.add_argument("--input-unit", required=True, choices=UNIT_TO_MM)
    prepare.add_argument("--scale", type=parse_scale, default=1.0, help="model:source, e.g. 1:100; default 1:1")
    prepare.add_argument("--printer", required=True, choices=[*PROFILES, "h2d"], help="h2d uses the conservative dual-nozzle common volume")
    prepare.add_argument("--xy-margin", type=float, default=5.0, metavar="MM", help="clearance on each XY side, default 5 mm")
    prepare.add_argument("--z-clearance", type=float, default=5.0, metavar="MM", help="clearance below the maximum Z, default 5 mm")
    prepare.add_argument("--output", required=True, type=Path, help="new or empty output directory")

    send = sub.add_parser("send", help="upload a sliced .gcode.3mf over LAN; does not start printing")
    send.add_argument("source", type=Path)
    send.add_argument("--host", required=True, help="printer IP address or hostname")
    send.add_argument("--access-code-env", default="BAMBU_ACCESS_CODE", help="environment variable containing the printer access code")
    send.add_argument("--remote-name", help="destination filename; existing files are not overwritten")
    send.add_argument("--timeout", type=float, default=30.0, help="network inactivity timeout in seconds, default 30")
    send.add_argument("--dry-run", action="store_true", help="validate the local sliced job without connecting or requiring credentials")
    send.add_argument("--json", action="store_true", help="print the result as JSON")
    return root


def main(argv: list[str] | None = None) -> int:
    # Keep Chinese profile notes and redirected JSON readable on Windows.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    args = parser().parse_args(argv)
    try:
        if args.command == "profiles":
            if args.json:
                print(json.dumps({name: asdict(profile) for name, profile in PROFILES.items()}, ensure_ascii=False, indent=2))
            else:
                for name, profile in PROFILES.items():
                    dimensions = " x ".join(f"{value:g}" for value in profile.usable_volume_mm)
                    print(f"{name}: {dimensions} mm | {profile.label}")
                    print(f"  {profile.notes}")
                print("h2d is an alias for h2d-dual. Defaults: 5 mm on each XY side and 5 mm below maximum Z.")
            return 0
        if args.command == "send":
            result = send_file(
                args.source.resolve(), host=args.host,
                access_code=os.environ.get(args.access_code_env),
                remote_name=args.remote_name, timeout=args.timeout, dry_run=args.dry_run,
            )
            if args.json:
                print(json.dumps(result, ensure_ascii=False, indent=2))
            elif args.dry_run:
                print(f"Validated {result['bytes']} bytes, plates {result['plates']}; no connection was made.")
            else:
                print(f"Uploaded {result['bytes']} bytes to {result['host']}:{result['remote_path']}.")
                print("Remote size confirmed. Printing has not been started.")
            return 0
        result = _prepare(args)
        dimensions = " x ".join(f"{value:g}" for value in result["working_volume_mm"])
        print(f"Prepared {len(result['parts'])} closed parts in mm; part envelope {dimensions} mm.")
        print(f"Output: {args.output.resolve()}")
        print("Import the STLs in millimeters. parts.json records assembly offsets and suggested bed placement.")
        return 0
    except (ValueError, OSError, RuntimeError) as exc:
        print(f"monkeyfab: {exc}", file=sys.stderr)
        return 2
