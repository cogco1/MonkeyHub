"""Build a Windows x64 MonkeyHub candidate from one exact Git commit.

This is a distribution builder, not a launcher or project writer. All build,
dependency and output files go to the supplied external directories. The
installed application uses apps/monkeyhub/run.py and launch-hub.ps1 unchanged.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import urllib.request
import zipfile


PYTHON_VERSION = "3.13.15"
PYTHON_SHA256 = "d1f04d990aee1253d8569e8e5104e30fa9f5fa830899f14843448872d936a2cf"
PYTHON_URL = f"https://www.python.org/ftp/python/{PYTHON_VERSION}/python-{PYTHON_VERSION}-embed-amd64.zip"
SOURCE_ROOT = Path(__file__).resolve().parents[1]
# Git, rather than the working directory, supplies these files. User runtime
# configuration, projects, credentials, caches and local WIP never enter a ZIP.
SOURCE_PATHS = (
    "archflow", "monkeyarch", "monkeydiagram", "monkeymonitor",
    "apps/archflow-studio/api", "apps/archflow-studio/web",
    "apps/archflow-studio/assets", "apps/archflow-studio/launch-studio.ps1",
    "apps/monkeyhub", "apps/shared-web", "OPEN_MONKEYHUB.cmd",
    "README.md", "pyproject.toml", "tools/create_project.py", "tools/run_project.py",
)


def run(command: list[str], *, cwd: Path | None = None, capture: bool = False,
        environment: dict[str, str] | None = None) -> str:
    result = subprocess.run(command, cwd=cwd, check=True, text=True,
                            encoding="utf-8", errors="replace",
                            env=environment,
                            stdout=subprocess.PIPE if capture else None)
    return result.stdout.strip() if capture else ""


def sha256(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def external(path: Path, source: Path) -> Path:
    path = path.resolve()
    if path == source or path.is_relative_to(source):
        raise ValueError(f"Choose an external directory, outside the source checkout: {path}")
    return path


def fetch_runtime(cache: Path) -> Path:
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache / f"python-{PYTHON_VERSION}-embed-amd64.zip"
    if not archive.exists():
        # An interrupted transfer cannot be mistaken for a complete cached file.
        temporary = archive.with_suffix(".download")
        with urllib.request.urlopen(PYTHON_URL, timeout=60) as response, temporary.open("wb") as output:
            shutil.copyfileobj(response, output)
        if sha256(temporary) != PYTHON_SHA256:
            raise ValueError("The Python runtime does not match the official SHA-256.")
        temporary.replace(archive)
    if sha256(archive) != PYTHON_SHA256:
        raise ValueError(f"The cached Python runtime has the wrong SHA-256: {archive}")
    return archive


def prepare_runtime(source: Path, destination: Path, cache: Path,
                    environment: dict[str, str] | None = None) -> None:
    """Vendor complete cp313 wheels; never install into the builder's Python."""
    destination.mkdir(parents=True)
    with zipfile.ZipFile(fetch_runtime(cache)) as archive:
        archive.extractall(destination)
    metadata = tomllib.loads((source / "pyproject.toml").read_text(encoding="utf-8"))
    requirements = [*metadata["project"]["dependencies"],
                    *metadata["project"]["optional-dependencies"]["cad-occt"]]
    requirement_args = ["-r", str(source / "apps/archflow-studio/api/requirements.txt")]
    hub_requirements = source / "apps/monkeyhub/api/requirements.txt"
    if hub_requirements.is_file():
        requirement_args.extend(["-r", str(hub_requirements)])
    wheels = cache / "wheels-cp313"
    wheels.mkdir(parents=True, exist_ok=True)
    target = ["--only-binary=:all:", "--platform", "win_amd64", "--python-version", "3.13",
              "--implementation", "cp", "--abi", "cp313"]
    pip = [sys.executable, "-m", "pip", "--disable-pip-version-check"]
    run([*pip, "download", *target, "--dest", str(wheels), *requirement_args, *requirements], environment=environment)
    site = destination / "Lib/site-packages"
    run([*pip, "install", *target, "--no-compile", "--no-index", "--find-links", str(wheels),
         "--target", str(site), *requirement_args, *requirements], environment=environment)
    # ._pth makes this interpreter independent of system Python/PYTHONPATH.
    # Keep import site: native wheels use their own .pth/DLL initialization.
    (destination / "python313._pth").write_text(
        "python313.zip\n.\nLib\\site-packages\n..\\..\n"
        "..\\..\\apps\\archflow-studio\\api\n..\\..\\apps\\monkeyhub\\api\nimport site\n",
        encoding="utf-8",
    )
    locked = sorted(f"{item.metadata['Name']}=={item.version}"
                    for item in importlib.metadata.distributions(path=[str(site)]))
    (destination.parent / "requirements-lock.txt").write_text("\n".join(locked) + "\n", encoding="utf-8")


def build_web(source: Path, node: Path, npm_cli: Path, environment: dict[str, str]) -> None:
    for relative in ("apps/archflow-studio/web", "apps/monkeyhub/web"):
        web = source / relative
        run([str(node), str(npm_cli), "ci", "--no-audit", "--no-fund"], cwd=web, environment=environment)
        run([str(node), str(npm_cli), "run", "build"], cwd=web, environment=environment)
        if not (web / "dist/index.html").is_file():
            raise ValueError(f"The production frontend was not built: {web}")


def collect_application(source: Path, bundle: Path, commit: str) -> None:
    bundle.mkdir()
    # These trees only contain the committed snapshot, before runtime writes.
    for name in ("archflow", "monkeyarch", "monkeydiagram", "monkeymonitor"):
        shutil.copytree(source / name, bundle / name)
    for relative in ("apps/archflow-studio/api/archflow_studio_api", "apps/archflow-studio/assets",
                     "apps/monkeyhub/api", "apps/monkeyhub/installer"):
        shutil.copytree(source / relative, bundle / relative,
                        ignore=shutil.ignore_patterns("__pycache__", "tests", "test_*", "third-party"))
    # Keep upstream license text and source labels, but shorten its distribution
    # paths so the installer does not depend on Windows long-path opt-in.
    notices = source / "apps/monkeyhub/installer/third-party"
    notice_target = bundle / "apps/monkeyhub/installer/third-party"
    notice_target.mkdir()
    links = {}
    for index, notice in enumerate(sorted(path for path in notices.rglob("*")
                                         if path.is_file() and path != notices / "README.md"), 1):
        filename = f"{index:03d}-{notice.name}"
        links[notice.relative_to(notices).as_posix()] = filename
        shutil.copy2(notice, notice_target / filename)
    notice_readme = (notices / "README.md").read_text(encoding="utf-8")
    notice_readme = re.sub(r"\]\(([^)]+)\)",
                          lambda match: "](" + links[match[1]] + ")" if match[1] in links else match[0],
                          notice_readme)
    (notice_target / "README.md").write_text(notice_readme, encoding="utf-8")
    for relative in ("apps/archflow-studio/web/dist", "apps/monkeyhub/web/dist"):
        shutil.copytree(source / relative, bundle / relative)
    for relative in ("apps/archflow-studio/launch-studio.ps1", "apps/monkeyhub/run.py",
                     "apps/monkeyhub/launch-hub.ps1", "OPEN_MONKEYHUB.cmd", "README.md", "pyproject.toml",
                     "apps/shared-web/src/appearance.js", "apps/shared-web/src/i18n.js",
                     "apps/shared-web/src/browserTranslator.js", "apps/shared-web/src/base.css",
                     "tools/create_project.py", "tools/run_project.py"):
        target = bundle / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / relative, target)
    shutil.copy2(source / "apps/monkeyhub/installer/INSTALL_MONKEYHUB.cmd", bundle / "INSTALL_MONKEYHUB.cmd")
    shutil.copy2(source / "apps/monkeyhub/installer/README.md", bundle / "INSTALLATION.md")
    (bundle / "source-version.txt").write_text(commit + "\n", encoding="utf-8")


def smoke_runtime(bundle: Path) -> None:
    python = bundle / "_runtime/python/python.exe"
    # No model services, project, GUI, or provider call is started by this check.
    run([str(python), "-B", "-c", (
        "import sys,ssl,fastapi,uvicorn,pydantic,pypdf,rhino3dm; "
        "from PIL import Image; from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox; "
        "import archflow,monkeyarch,monkeydiagram,monkeymonitor,archflow_studio_api; "
        "assert sys.version_info[:3]==(3,13,15); "
        "assert not BRepPrimAPI_MakeBox(1,2,3).Shape().IsNull(); "
        "assert Image.new('RGB',(2,2)).size==(2,2); "
        "assert rhino3dm.File3dm.fromByteArray(rhino3dm.File3dm().toByteArray()) is not None; "
        "print('Bundled Python, API, image/PDF and OCCT/3DM imports: PASS')"
    )], cwd=bundle)
    run([str(python), "-B", str(bundle / "apps/monkeyhub/run.py"), "--help"], cwd=bundle)


def package(source_root: Path, source_ref: str, staging: Path, output: Path,
            cache: Path, node: Path, npm_cli: Path) -> Path:
    if sys.platform != "win32":
        raise ValueError("Build and verify this Windows candidate on Windows x64.")
    source_root = source_root.resolve()
    staging, output, cache = (external(path, source_root) for path in (staging, output, cache))
    commit = run(["git", "rev-parse", "--verify", f"{source_ref}^{{commit}}"], cwd=source_root, capture=True)
    staging.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    build = Path(tempfile.mkdtemp(prefix=f"candidate-{commit[:12]}-", dir=staging))
    temporary = build / "tmp"
    temporary.mkdir()
    environment = dict(os.environ, TEMP=str(temporary), TMP=str(temporary),
                       PATH=str(node.resolve().parent) + os.pathsep + os.environ.get("PATH", ""),
                       PIP_CACHE_DIR=str(cache / "pip-cache"), npm_config_cache=str(cache / "npm-cache"))
    print(f"Source: {commit}\nBuild directory: {build}", flush=True)
    snapshot = build / "source.zip"
    run(["git", "archive", "--format=zip", f"--output={snapshot}", commit, "--", *SOURCE_PATHS], cwd=source_root)
    source = build / "source"
    with zipfile.ZipFile(snapshot) as archive:
        archive.extractall(source)
    build_web(source, node, npm_cli, environment)
    bundle = build / f"MonkeyHub-{commit[:12]}-windows-x64"
    collect_application(source, bundle, commit)
    prepare_runtime(source, bundle / "_runtime/python", cache, environment)
    smoke_runtime(bundle)
    # Version + exact inputs are distribution metadata, not project records.
    (bundle / "build-info.json").write_text(json.dumps({
        "sourceCommit": commit, "target": "windows-x64", "channel": "candidate",
        "pythonVersion": PYTHON_VERSION, "pythonUrl": PYTHON_URL, "pythonSha256": PYTHON_SHA256,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    zip_path = build / f"{bundle.name}-candidate.zip"
    with zipfile.ZipFile(zip_path, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(bundle.rglob("*")):
            if path.is_file():
                archive.write(path, (Path(bundle.name) / path.relative_to(bundle)).as_posix())
    # The archive is finalized on the staging drive before consuming output space.
    if shutil.disk_usage(output).free < zip_path.stat().st_size + 64 * 1024 * 1024:
        raise ValueError(f"Not enough free space for the completed ZIP in {output}; it remains at {zip_path}")
    final = output / zip_path.name
    if final.exists():
        raise ValueError(f"A candidate with this source name already exists; choose another output directory: {final}")
    shutil.copy2(zip_path, final)
    (output / f"{final.name}.sha256").write_text(f"{sha256(final)}  {final.name}\n", encoding="utf-8")
    print(f"Candidate: {final}\nBytes: {final.stat().st_size}\nUnpacked: {bundle}", flush=True)
    return final


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=SOURCE_ROOT)
    parser.add_argument("--source-ref", default="HEAD", help="exact integrated commit or ref; working files are not packaged")
    parser.add_argument("--staging-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--cache-dir", type=Path, help="defaults to <staging-dir>/cache")
    parser.add_argument("--node", type=Path, default=Path(shutil.which("node") or "node.exe"))
    parser.add_argument("--npm-cli", type=Path, help="path to npm/bin/npm-cli.js; no shell or npm.cmd interpolation")
    args = parser.parse_args(argv)
    npm_cli = args.npm_cli or args.node.resolve().parent / "node_modules/npm/bin/npm-cli.js"
    try:
        if not args.node.is_file() or not npm_cli.is_file():
            raise ValueError("The builder needs Node and npm-cli.js; supply --node and --npm-cli.")
        package(args.source_root, args.source_ref, args.staging_dir, args.output_dir,
                args.cache_dir or args.staging_dir / "cache", args.node, npm_cli)
    except (OSError, ValueError, subprocess.CalledProcessError, zipfile.BadZipFile) as error:
        parser.exit(1, f"package_monkeyapps: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
