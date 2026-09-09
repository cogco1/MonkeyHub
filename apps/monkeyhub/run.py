"""Run the local Hub or one of its fixed child services, including embedded Python."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
for directory in (ROOT, ROOT / "apps/archflow-studio/api", ROOT / "apps/monkeyhub/api"):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))


def main() -> None:
    args = sys.argv[1:]
    if args[:1] == ["--service"]:
        if len(args) < 2:
            raise SystemExit("--service requires studio or monitor")
        service, args = args[1], args[2:]
        if service == "studio":
            from archflow_studio_api.main import main as serve
        elif service == "monitor":
            from monkeymonitor.__main__ import main as serve
        else:
            raise SystemExit("--service must be studio or monitor")
    else:
        from monkeyhub_api.main import main as serve
    serve(args)


if __name__ == "__main__":
    main()
