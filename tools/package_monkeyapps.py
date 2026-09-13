"""Build a Windows x64 MonkeyHub candidate, optionally with an exact MonkeyFab commit.

This is a distribution builder, not a launcher or project writer. All build,
dependency and output files go to the selected external directories. Use
--configure --workspace-root once to remember a root in personal Git config;
later builds reuse task directories and caches there. The
installed application uses the existing apps/monkeyhub/run.py and launch-hub.ps1.
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
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))
from tools.workspace import (
    WORKSPACE_CONFIG_KEY, configured_root, configure_root, task_name, task_paths, validate_root,
)
# Git, rather than the working directory, supplies these files. User runtime
# configuration, projects, credentials, caches and local WIP never enter a ZIP.
SOURCE_PATHS = (
    "archflow", "monkeyarch", "monkeydiagram", "monkeymonitor",
    "apps/archflow-studio/api", "apps/archflow-studio/web",
    "apps/archflow-studio/assets", "apps/archflow-studio/launch-studio.ps1",
    "apps/monkeyhub", "apps/shared-web", "OPEN_MONKEYHUB.cmd",
    "README.md", "pyproject.toml", "tools/create_project.py", "tools/run_project.py",
    "governance/module_registry.json",
)
MONKEYFAB_SOURCE_PATHS = ("src/monkeyfab", "pyproject.toml", "README.md")


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
                    environment: dict[str, str] | None = None,
                    monkeyfab_source: Path | None = None) -> None:
    """Vendor complete cp313 wheels; never install into the builder's Python."""
    destination.mkdir(parents=True)
    with zipfile.ZipFile(fetch_runtime(cache)) as archive:
        archive.extractall(destination)
    metadata = tomllib.loads((source / "pyproject.toml").read_text(encoding="utf-8"))
    requirements = [*metadata["project"]["dependencies"],
                    *metadata["project"]["optional-dependencies"]["cad-occt"]]
    if monkeyfab_source is not None:
        fab_metadata = tomllib.loads((monkeyfab_source / "pyproject.toml").read_text(encoding="utf-8"))
        requirements.extend(fab_metadata["project"]["dependencies"])
        requirements.extend(fab_metadata["project"]["optional-dependencies"]["send"])
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
        "..\\..\\apps\\archflow-studio\\api\n..\\..\\apps\\monkeyhub\\api\n"
        + ("..\\..\\apps\\monkeyfab\\src\n" if monkeyfab_source is not None else "")
        + "import site\n",
        encoding="utf-8",
    )
    locked = sorted(f"{item.metadata['Name']}=={item.version}"
                    for item in importlib.metadata.distributions(path=[str(site)]))
    (destination.parent / "requirements-lock.txt").write_text("\n".join(locked) + "\n", encoding="utf-8")


def build_web(source: Path, node: Path, npm_cli: Path, environment: dict[str, str]) -> None:
    # The adapter uses the user's installed Codex through CODEX_PATH. Its
    # optional native Codex copies are not needed in the Hub distribution.
    run([str(node), str(npm_cli), "ci", "--omit=dev", "--omit=optional", "--no-audit", "--no-fund"],
        cwd=source / "apps/monkeyhub", environment=environment)
    for relative in ("apps/archflow-studio/web", "apps/monkeyhub/web"):
        web = source / relative
        run([str(node), str(npm_cli), "ci", "--no-audit", "--no-fund"], cwd=web, environment=environment)
        run([str(node), str(npm_cli), "run", "build"], cwd=web, environment=environment)
        if not (web / "dist/index.html").is_file():
            raise ValueError(f"The production frontend was not built: {web}")


def collect_web_notices(source: Path, target: Path, supplemental_links: dict[str, str]) -> str:
    """Copy the production notices named by the frontend and adapter lockfiles."""
    rows = []
    copied: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for application, relative_root in (
        ("archflow-studio", "apps/archflow-studio/web"),
        ("monkeyhub", "apps/monkeyhub/web"),
        ("monkeyhub ACP", "apps/monkeyhub"),
    ):
        web = source / relative_root
        locked = json.loads((web / "package-lock.json").read_text(encoding="utf-8"))
        for relative, metadata in sorted(locked["packages"].items()):
            if not relative or metadata.get("dev"):
                continue
            dependency = web / relative
            if not (dependency / "package.json").is_file():
                if metadata.get("optional"):
                    continue  # Optional native packages for another platform.
                raise ValueError(f"Production dependency is not installed: {application}/{relative}")
            package = json.loads((dependency / "package.json").read_text(encoding="utf-8"))
            name, version = package["name"], package["version"]
            identity = (name, version)
            if identity not in copied:
                stem = re.sub(r"[^A-Za-z0-9._-]+", "-", f"{name}-{version}").lstrip("-")
                texts = sorted(path for path in dependency.iterdir() if path.is_file()
                               and re.match(r"^(licen[cs]e|copying|notice)([._-].*)?$", path.name, re.I))
                copied[identity] = []
                for index, text in enumerate(texts, 1):
                    filename = f"web-{stem}-{index}-{text.name}.txt"
                    shutil.copy2(text, target / filename)
                    copied[identity].append((text.name, filename))
                if not texts:
                    supplement = f"web-supplement/{stem}-LICENSE.txt"
                    if name == "@openai/codex" and re.fullmatch(r"\d+\.\d+\.\d+", version):
                        # This npm shim omits its upstream notices. Retrieve
                        # the exact locked release's originals at build time.
                        for label in ("LICENSE", "NOTICE"):
                            filename = f"web-{stem}-{label}.txt"
                            url = f"https://raw.githubusercontent.com/openai/codex/rust-v{version}/{label}"
                            with urllib.request.urlopen(url, timeout=60) as response, (target / filename).open("wb") as output:
                                shutil.copyfileobj(response, output)
                            copied[identity].append((f"{label} (upstream release)", filename))
                    elif supplement not in supplemental_links:
                        raise ValueError(f"No upstream license text for {name} {version}; add its exact-version web supplement")
                    else:
                        copied[identity].append(("LICENSE (upstream supplement)", supplemental_links[supplement]))
            row = f"- {application}: {name} {version} — " + ", ".join(
                f"[{label}]({filename})" for label, filename in copied[identity])
            if row not in rows:
                rows.append(row)
    return "\n".join(rows) + "\n"


def collect_application(source: Path, bundle: Path, commit: str,
                        monkeyfab_source: Path | None = None, *, node: Path) -> None:
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
    # Supplemental/font READMEs keep working after their source directories
    # have been flattened; the license texts themselves stay byte-identical.
    for relative, filename in links.items():
        if Path(relative).name.lower() == "readme.md":
            content = (notices / relative).read_text(encoding="utf-8")
            content = re.sub(r"\]\(([^)]+)\)", lambda match: "](" + links.get(
                (Path(relative).parent / match[1]).as_posix(), match[1]) + ")", content)
            (notice_target / filename).write_text(content, encoding="utf-8")
    notice_readme = (notices / "README.md").read_text(encoding="utf-8")
    notice_readme = re.sub(r"\]\(([^)]+)\)",
                          lambda match: "](" + links[match[1]] + ")" if match[1] in links else match[0],
                          notice_readme)
    notice_readme += "\n## 前端与 ACP 随包许可\n\n原文来自各 package-lock.json 安装的生产依赖。\n\n"
    notice_readme += collect_web_notices(source, notice_target, links)
    node_version = run([str(node), "--version"], capture=True)
    if not re.fullmatch(r"v\d+\.\d+\.\d+", node_version):
        raise ValueError("The selected Node runtime did not report a release version.")
    node_license = f"node-{node_version}-LICENSE.txt"
    node_license_url = f"https://raw.githubusercontent.com/nodejs/node/{node_version}/LICENSE"
    with urllib.request.urlopen(node_license_url, timeout=60) as response, (notice_target / node_license).open("wb") as output:
        shutil.copyfileobj(response, output)
    notice_readme += f"\n- Node.js {node_version}: [LICENSE and bundled dependency notices]({node_license})\n"
    (notice_target / "README.md").write_text(notice_readme, encoding="utf-8")
    node_runtime = bundle / "_runtime/node"
    node_runtime.mkdir(parents=True)
    shutil.copy2(node, node_runtime / "node.exe")
    shutil.copytree(source / "apps/monkeyhub/node_modules", bundle / "apps/monkeyhub/node_modules",
                    ignore=shutil.ignore_patterns(".bin"))
    for relative in ("apps/archflow-studio/web/dist", "apps/monkeyhub/web/dist"):
        shutil.copytree(source / relative, bundle / relative)
    # The capability index the Studio serves at /api/capabilities is read from
    # this registry beside the application, so the installed product answers
    # from the same file the checkout does instead of an embedded copy.
    for relative in ("apps/archflow-studio/launch-studio.ps1", "apps/monkeyhub/run.py",
                     "apps/monkeyhub/launch-hub.ps1", "OPEN_MONKEYHUB.cmd", "pyproject.toml",
                     "governance/module_registry.json",
                     "apps/shared-web/src/appearance.js", "apps/shared-web/src/i18n.js",
                     "apps/shared-web/src/browserTranslator.js", "apps/shared-web/src/base.css",
                     "tools/create_project.py", "tools/run_project.py"):
        target = bundle / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / relative, target)
    shutil.copy2(source / "apps/monkeyhub/installer/INSTALL_MONKEYHUB.cmd", bundle / "INSTALL_MONKEYHUB.cmd")
    shutil.copy2(source / "apps/monkeyhub/installer/README.md", bundle / "README.md")
    shutil.copy2(source / "apps/monkeyhub/installer/README.md", bundle / "INSTALLATION.md")
    if monkeyfab_source is not None:
        shutil.copytree(monkeyfab_source, bundle / "apps/monkeyfab")
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
        "assert rhino3dm.File3dm.Decode(rhino3dm.File3dm().Encode()) is not None; "
        "print('Bundled Python, API, image/PDF and OCCT/3DM imports: PASS')"
    )], cwd=bundle)
    run([str(python), "-B", str(bundle / "apps/monkeyhub/run.py"), "--help"], cwd=bundle)
    run([str(python), "-B", "-c", (
        "from pathlib import Path; from monkeyhub_api.chat import _codex_acp_command; "
        "root=Path.cwd(); command=_codex_acp_command(); "
        "assert command and Path(command[0])==root/'_runtime/node/node.exe'; "
        "assert Path(command[1]).is_relative_to(root); "
        "print('Bundled ACP SDK, adapter and Node resolution: PASS')"
    )], cwd=bundle)
    run([str(bundle / "_runtime/node/node.exe"), "--check",
         str(bundle / "apps/monkeyhub/node_modules/@agentclientprotocol/codex-acp/dist/index.js")], cwd=bundle)
    if (bundle / "apps/monkeyfab/src/monkeyfab").is_dir():
        profiles = json.loads(run(
            [str(python), "-B", "-m", "monkeyfab", "profiles", "--json"], cwd=bundle, capture=True))
        if profiles["h2s"]["usable_volume_mm"] != [340.0, 320.0, 340.0]:
            raise ValueError("The bundled MonkeyFab H2S profile is not the expected 340 x 320 x 340 mm.")
        # Exercise native geometry and CLI I/O outside the distributable tree.
        with tempfile.TemporaryDirectory(prefix="monkeyfab-smoke-", dir=bundle.parent) as temporary:
            scratch = Path(temporary)
            run([str(python), "-B", "-c", (
                "from pathlib import Path; import sys,trimesh,manifold3d; "
                "from zipfile import ZipFile; "
                "from bambulabs_api.ftp_client import ImplicitFTP_TLS; "
                "root=Path(sys.argv[1]); "
                "trimesh.creation.box(extents=(680,40,20)).export(root/'box.stl'); "
                "job=ZipFile(root/'sample.gcode.3mf','w'); "
                "job.writestr('Metadata/plate_1.gcode','G90\\n'); job.close()"
            ), str(scratch)], cwd=bundle)
            output = scratch / "prepared"
            run([str(python), "-B", "-m", "monkeyfab", "prepare", str(scratch / "box.stl"),
                 "--input-unit", "mm", "--printer", "h2s", "--output", str(output)], cwd=bundle)
            prepared = json.loads((output / "parts.json").read_text(encoding="utf-8"))
            if (len(prepared["parts"]) != 3
                    or prepared["working_volume_mm"] != [330.0, 310.0, 335.0]
                    or abs(sum(part["volume_mm3"] for part in prepared["parts"]) - 544000.0) > 0.001
                    or any(not (output / part["file"]).is_file() for part in prepared["parts"])):
                raise ValueError("The bundled MonkeyFab did not prepare the expected closed H2S parts.")
            sent = json.loads(run(
                [str(python), "-B", "-m", "monkeyfab", "send", str(scratch / "sample.gcode.3mf"),
                 "--host", "192.0.2.1", "--dry-run", "--json"], cwd=bundle, capture=True))
            if sent["status"] != "validated" or sent["print_started"] or sent["plates"] != [1]:
                raise ValueError("The bundled MonkeyFab local send dry-run failed.")
        print("Bundled MonkeyFab H2S prepare and local send dry-run: PASS", flush=True)


def build_desktop(source: Path, bundle: Path, commit: str, cargo: Path,
                  environment: dict[str, str]) -> dict[str, str]:
    """Compile the thin host from the same selected snapshot as its Hub."""
    desktop = source / "apps/monkeyhub/desktop"
    target = source.parent / "desktop-target"
    build_environment = dict(environment, ARCHFLOW_SOURCE_REVISION=commit)
    run([str(cargo), "build", "--locked", "--release", "--target-dir", str(target)],
        cwd=desktop, environment=build_environment)
    executable = target / "release/MonkeyArch.exe"
    if not executable.is_file():
        raise ValueError(f"The desktop host was not built: {executable}")
    identity = json.loads(run([str(executable), "--version"], capture=True, environment=build_environment))
    package_info = tomllib.loads((desktop / "Cargo.toml").read_text(encoding="utf-8"))["package"]
    if identity.get("sourceRevision") != commit or identity.get("version") != package_info["version"]:
        raise ValueError("The desktop executable does not match the selected source snapshot/version.")
    shutil.copy2(executable, bundle / "MonkeyArch.exe")
    shutil.copy2(desktop / "Cargo.lock", bundle / "_runtime/desktop-Cargo.lock")
    return {
        "version": package_info["version"], "sourceCommit": commit,
        "cargoVersion": run([str(cargo), "--version"], capture=True, environment=build_environment),
        "cargoLockSha256": sha256(desktop / "Cargo.lock"),
        "executableSha256": sha256(bundle / "MonkeyArch.exe"),
    }


def package(source_root: Path, source_ref: str, staging: Path, output: Path,
            cache: Path, node: Path, npm_cli: Path,
            monkeyfab_source: Path | None = None, monkeyfab_ref: str | None = None,
            *, desktop: bool = False, cargo: Path | None = None) -> Path:
    if sys.platform != "win32":
        raise ValueError("Build and verify this Windows candidate on Windows x64.")
    source_root = source_root.resolve()
    staging, output, cache = (external(path, source_root) for path in (staging, output, cache))
    commit = run(["git", "rev-parse", "--verify", f"{source_ref}^{{commit}}"], cwd=source_root, capture=True)
    if (monkeyfab_source is None) != (monkeyfab_ref is None):
        raise ValueError("Supply --monkeyfab-source and --monkeyfab-ref together.")
    fab_commit = None
    if monkeyfab_source is not None:
        monkeyfab_source = monkeyfab_source.resolve()
        for path in (staging, output, cache):
            external(path, monkeyfab_source)
        fab_commit = run(["git", "rev-parse", "--verify", f"{monkeyfab_ref}^{{commit}}"],
                         cwd=monkeyfab_source, capture=True)
    version = commit[:12] + (f"-fab-{fab_commit[:12]}" if fab_commit else "")
    if desktop:
        if cargo is None or not cargo.is_file():
            raise ValueError("The desktop build needs Rust/MSVC Cargo; supply --cargo or add it to PATH.")
        version += "-desktop"
    staging.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    build = Path(tempfile.mkdtemp(prefix=f"candidate-{commit[:12]}-", dir=staging))
    temporary = build / "tmp"
    temporary.mkdir()
    environment = dict(os.environ, TEMP=str(temporary), TMP=str(temporary),
                       PATH=str(node.resolve().parent) + os.pathsep + os.environ.get("PATH", ""),
                       PIP_CACHE_DIR=str(cache / "pip-cache"), npm_config_cache=str(cache / "npm-cache"))
    if desktop:
        environment.setdefault("CARGO_HOME", str(cache / "cargo"))
    print(f"Source: {commit}\nBuild directory: {build}", flush=True)
    snapshot = build / "source.zip"
    run(["git", "archive", "--format=zip", f"--output={snapshot}", commit, "--", *SOURCE_PATHS], cwd=source_root)
    source = build / "source"
    with zipfile.ZipFile(snapshot) as archive:
        archive.extractall(source)
    fab_snapshot = None
    if monkeyfab_source is not None:
        fab_archive = build / "monkeyfab-source.zip"
        run(["git", "archive", "--format=zip", f"--output={fab_archive}", fab_commit,
             "--", *MONKEYFAB_SOURCE_PATHS], cwd=monkeyfab_source)
        fab_snapshot = build / "monkeyfab-source"
        with zipfile.ZipFile(fab_archive) as archive:
            archive.extractall(fab_snapshot)
        print(f"MonkeyFab source: {fab_commit}", flush=True)
    build_web(source, node, npm_cli, environment)
    bundle = build / f"MonkeyHub-{version}-windows-x64"
    collect_application(source, bundle, commit, fab_snapshot, node=node)
    prepare_runtime(source, bundle / "_runtime/python", cache, environment, fab_snapshot)
    smoke_runtime(bundle)
    desktop_info = build_desktop(source, bundle, commit, cargo, environment) if desktop else None
    # Version + exact inputs are distribution metadata, not project records.
    (bundle / "build-info.json").write_text(json.dumps({
        "sourceCommit": commit, "target": "windows-x64", "channel": "candidate",
        **({"monkeyFabCommit": fab_commit} if fab_commit else {}),
        **({"desktop": desktop_info} if desktop_info else {}),
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
    parser.add_argument("--monkeyfab-source", type=Path, help="independent MonkeyFab Git checkout; pair with --monkeyfab-ref")
    parser.add_argument("--monkeyfab-ref", help="MonkeyFab commit or ref to include; working files are not packaged")
    parser.add_argument("--workspace-root", type=Path,
                        help=f"external build root; defaults to Git config {WORKSPACE_CONFIG_KEY}")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--configure", action="store_true",
                        help="save --workspace-root in personal Git config and show paths without building")
    action.add_argument("--show-paths", action="store_true", help="show resolved paths without creating directories or building")
    parser.add_argument("--task", help="task directory name; defaults to the current branch")
    parser.add_argument("--staging-dir", type=Path, help="overrides <workspace-root>/temp/package-monkeyapps/<task>")
    parser.add_argument("--output-dir", type=Path, help="overrides <workspace-root>/packages/<task>")
    parser.add_argument("--cache-dir", type=Path,
                        help="overrides <workspace-root>/cache/package-monkeyapps; otherwise defaults to <staging-dir>/cache")
    parser.add_argument("--node", type=Path, default=Path(shutil.which("node") or "node.exe"))
    parser.add_argument("--npm-cli", type=Path, help="path to npm/bin/npm-cli.js; no shell or npm.cmd interpolation")
    parser.add_argument("--desktop", action="store_true", help="build MonkeyArch.exe from the same snapshot using Rust/MSVC")
    parser.add_argument("--cargo", type=Path, default=Path(shutil.which("cargo") or "cargo.exe"),
                        help="Cargo executable for --desktop; build dependencies stay outside the source checkout")
    args = parser.parse_args(argv)
    npm_cli = args.npm_cli or args.node.resolve().parent / "node_modules/npm/bin/npm-cli.js"
    try:
        source = args.source_root.resolve()
        if args.configure and args.workspace_root is None:
            raise ValueError("--configure requires --workspace-root.")
        workspace = validate_root(source, args.workspace_root.expanduser()) if args.workspace_root else configured_root(source)
        task = None
        if workspace is not None:
            task = task_name(source, args.task)
            selected = task_paths(workspace, task)
            args.staging_dir = args.staging_dir or selected["stagingDir"]
            args.output_dir = args.output_dir or selected["outputDir"]
            args.cache_dir = args.cache_dir or selected["cacheDir"]
        elif args.task is not None:
            raise ValueError("--task requires a configured or explicit --workspace-root.")
        if args.staging_dir is None or args.output_dir is None:
            raise ValueError("Run --configure --workspace-root <external directory> once, or supply --staging-dir and --output-dir.")
        staging, output, cache = (external(path, source) for path in (
            args.staging_dir, args.output_dir, args.cache_dir or args.staging_dir / "cache"))
        if args.monkeyfab_source is not None:
            for path in (staging, output, cache):
                external(path, args.monkeyfab_source.resolve())
        if args.configure:
            configure_root(source, workspace)
        if args.configure or args.show_paths:
            print(json.dumps({
                "sourceRoot": str(source), "sourceRef": args.source_ref,
                "workspaceRoot": str(workspace) if workspace else None, "task": task,
                "stagingDir": str(staging), "outputDir": str(output), "cacheDir": str(cache),
                "projectData": "Not accessed by this packaging command.",
            }, ensure_ascii=False, indent=2))
            return 0
        if not args.node.is_file() or not npm_cli.is_file():
            raise ValueError("The builder needs Node and npm-cli.js; supply --node and --npm-cli.")
        package(source, args.source_ref, staging, output, cache, args.node, npm_cli,
                args.monkeyfab_source, args.monkeyfab_ref, desktop=args.desktop, cargo=args.cargo)
    except (OSError, ValueError, subprocess.CalledProcessError, zipfile.BadZipFile) as error:
        parser.exit(1, f"package_monkeyapps: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
