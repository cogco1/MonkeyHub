"""Build a Windows x64 MonkeyHub candidate including Fab from one exact commit.

This is a distribution builder, not a launcher or project writer. All build,
dependency and output files go to the selected external directories. Use
--configure --workspace-root once to remember a root in personal Git config;
later builds reuse task directories and caches there. The
installed application uses the existing apps/monkeyhub/run.py and launch-hub.ps1.

Each build finishes with release evidence beside the candidate ZIP: a CycloneDX
SBOM read from the assembled bundle, and a ReleaseManifest@1 that views the same
build-info.json over a closed table of the distributed files. --verify re-hashes
those files, opens the archive to check the build-info.json and SBOM copies it
carries against the digests the manifest binds them to, and reports every miss.
Nothing here is signed: the manifest records candidate-unsigned, and a verified
release proves only that the files match that manifest, never who produced it.
"""
from __future__ import annotations

import argparse
from collections.abc import Iterable, Iterator
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
import urllib.parse
import urllib.request
import zipfile


PYTHON_VERSION = "3.13.15"
PYTHON_SHA256 = "d1f04d990aee1253d8569e8e5104e30fa9f5fa830899f14843448872d936a2cf"
PYTHON_URL = f"https://www.python.org/ftp/python/{PYTHON_VERSION}/python-{PYTHON_VERSION}-embed-amd64.zip"
SOURCE_ROOT = Path(__file__).resolve().parents[1]
# Release evidence beside the candidate. build-info.json keeps the single build
# identity; the manifest is a derived view of it plus the closed artifact table,
# and the SBOM is read from the assembled bundle, never from a kept list.
RELEASE_MANIFEST_SCHEMA = "ReleaseManifest@1"
SBOM_SPEC_VERSION = "1.6"
SBOM_NAME = "sbom.cyclonedx.json"
SHIPPED_IN = "monkeyhub:shippedIn"
BUILD_INPUT = "monkeyhub:buildInput"
PYTHON_SITE = "_runtime/python/Lib/site-packages"
CRATES_IO = "registry+https://github.com/rust-lang/crates.io-index"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))
from tools.workspace import (
    WORKSPACE_CONFIG_KEY, configured_root, configure_root, task_name, task_paths, validate_root,
)
from apps.monkeyhub.installer.patch import create_patch
# Git, rather than the working directory, supplies these files. User runtime
# configuration, projects, credentials, caches and local WIP never enter a ZIP.
SOURCE_PATHS = (
    "archflow", "monkeyarch", "monkeydiagram", "monkeymonitor", "monkeycontrol",
    "apps/archflow-studio/api",
    "apps/archflow-studio/assets",
    "apps/monkeyhub", "apps/monkeyfab", "apps/shared-web", "OPEN_MONKEYHUB.cmd",
    "README.md", "SECURITY.md", "pyproject.toml", "tools/create_project.py", "tools/run_project.py",
    "governance/module_registry.json",
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
    fab_metadata = tomllib.loads((source / "apps/monkeyfab/pyproject.toml").read_text(encoding="utf-8"))
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
        "..\\..\\apps\\monkeyfab\\src\nimport site\n",
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
    for relative in ("apps/monkeyhub/web",):
        web = source / relative
        run([str(node), str(npm_cli), "ci", "--no-audit", "--no-fund"], cwd=web, environment=environment)
        run([str(node), str(npm_cli), "run", "build"], cwd=web, environment=environment)
        if not (web / "dist/index.html").is_file():
            raise ValueError(f"The production frontend was not built: {web}")


def installed_node_packages(lock_root: Path, tree: Path, label: str) -> Iterator[tuple[Path, dict]]:
    """Yield each production package this lockfile pins, read where it is installed.

    Licences and the SBOM describe the same inventory, so both read the actual
    installed ``package.json`` rather than the lockfile's own metadata. ``tree``
    is the assembled bundle when the packages ship as files.
    """
    locked = json.loads((lock_root / "package-lock.json").read_text(encoding="utf-8"))
    for relative, metadata in sorted(locked["packages"].items()):
        if not relative or metadata.get("dev"):
            continue
        dependency = tree / relative
        if not (dependency / "package.json").is_file():
            if metadata.get("optional"):
                continue  # Optional native packages for another platform.
            raise ValueError(f"Production dependency is not installed: {label}/{relative}")
        yield dependency, json.loads((dependency / "package.json").read_text(encoding="utf-8"))


def collect_web_notices(source: Path, target: Path, supplemental_links: dict[str, str]) -> str:
    """Copy the production notices named by the frontend and adapter lockfiles."""
    rows = []
    copied: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for application, relative_root in (
        ("monkeyhub", "apps/monkeyhub/web"),
        ("monkeyhub ACP", "apps/monkeyhub"),
    ):
        web = source / relative_root
        for dependency, package in installed_node_packages(web, web, application):
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


def collect_application(source: Path, bundle: Path, commit: str, *, node: Path) -> None:
    bundle.mkdir()
    # These trees only contain the committed snapshot, before runtime writes.
    # monkeycontrol travels with its PowerShell hosts: the installed Hub
    # imports it to expose the computer-use routes, and without the hosts the
    # package would be there and still unable to reach a desktop.
    for name in ("archflow", "monkeyarch", "monkeydiagram", "monkeymonitor", "monkeycontrol"):
        shutil.copytree(source / name, bundle / name)
    for relative in ("apps/archflow-studio/api/archflow_studio_api", "apps/archflow-studio/assets",
                     "apps/monkeyhub/api", "apps/monkeyhub/installer", "apps/monkeyfab"):
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
    for relative in ("apps/monkeyhub/web/dist",):
        shutil.copytree(source / relative, bundle / relative)
    # The capability index the Studio serves at /api/capabilities is read from
    # this registry beside the application, so the installed product answers
    # from the same file the checkout does instead of an embedded copy.
    for relative in ("apps/monkeyhub/run.py",
                     "apps/monkeyhub/launch-hub.ps1", "OPEN_MONKEYHUB.cmd", "pyproject.toml",
                     # The security-reporting route travels with the distributed bundle,
                     # not only with a checkout of the public repository.
                     "governance/module_registry.json", "SECURITY.md",
                     "apps/shared-web/src/appearance.js", "apps/shared-web/src/i18n.js",
                     "apps/shared-web/src/browserTranslator.js", "apps/shared-web/src/base.css",
                     "tools/create_project.py", "tools/run_project.py"):
        target = bundle / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / relative, target)
    shutil.copy2(source / "apps/monkeyhub/installer/INSTALL_MONKEYHUB.cmd", bundle / "INSTALL_MONKEYHUB.cmd")
    shutil.copy2(source / "apps/monkeyhub/installer/README.md", bundle / "README.md")
    shutil.copy2(source / "apps/monkeyhub/installer/README.md", bundle / "INSTALLATION.md")
    (bundle / "source-version.txt").write_text(commit + "\n", encoding="utf-8")


def smoke_runtime(bundle: Path) -> None:
    python = bundle / "_runtime/python/python.exe"
    # No model services, project, GUI, or provider call is started by this check.
    run([str(python), "-B", "-c", (
        "import sys,ssl,fastapi,uvicorn,pydantic,pypdf,rhino3dm; "
        "from PIL import Image; from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox; "
        "import archflow,monkeyarch,monkeydiagram,monkeymonitor,monkeycontrol,archflow_studio_api; "
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
    executable = target / "release/MonkeyHub.exe"
    if not executable.is_file():
        raise ValueError(f"The desktop host was not built: {executable}")
    identity = json.loads(run([str(executable), "--version"], capture=True, environment=build_environment))
    package_info = tomllib.loads((desktop / "Cargo.toml").read_text(encoding="utf-8"))["package"]
    if identity.get("sourceRevision") != commit or identity.get("version") != package_info["version"]:
        raise ValueError("The desktop executable does not match the selected source snapshot/version.")
    shutil.copy2(executable, bundle / "MonkeyHub.exe")
    shutil.copy2(desktop / "Cargo.lock", bundle / "_runtime/desktop-Cargo.lock")
    return {
        "version": package_info["version"], "sourceCommit": commit,
        "cargoVersion": run([str(cargo), "--version"], capture=True, environment=build_environment),
        "cargoLockSha256": sha256(desktop / "Cargo.lock"),
        "executableSha256": sha256(bundle / "MonkeyHub.exe"),
    }


def runtime_inventory(source: Path, bundle: Path) -> dict[str, object]:
    """Describe actual shipped runtimes/assets in the existing build manifest."""
    adapter = json.loads((bundle / "apps/monkeyhub/node_modules/@agentclientprotocol/codex-acp/package.json").read_text(encoding="utf-8"))
    return {
        "nodeVersion": run([str(bundle / "_runtime/node/node.exe"), "--version"], capture=True),
        "pythonRequirements": {
            "path": "_runtime/requirements-lock.txt",
            "sha256": sha256(bundle / "_runtime/requirements-lock.txt"),
        },
        "acpAdapter": {
            "name": adapter["name"], "version": adapter["version"],
            "packageLockSha256": sha256(source / "apps/monkeyhub/package-lock.json"),
        },
        "frontends": {name: {
            "packageLockSha256": sha256(source / f"apps/{name}/web/package-lock.json"),
            "files": {path.relative_to(bundle).as_posix(): sha256(path)
                      for path in sorted((bundle / f"apps/{name}/web/dist").rglob("*")) if path.is_file()},
        } for name in ("monkeyhub",)},
        "externalDependencies": ["Microsoft Edge WebView2 (desktop)", "Codex or Claude CLI and provider credentials",
                                 "Rhino/Blender when that backend is selected"],
    }


def purl(kind: str, name: str, version: str, *, namespace: str = "",
         qualifiers: dict[str, str] | None = None) -> str:
    """One package-url in the canonical form of the purl specification.

    Qualifier keys are sorted and their values percent-encoded apart from the
    scheme/digest colon, as the specification's own examples are written.
    """
    segments = [f"pkg:{kind}"]
    if namespace:
        segments.append(urllib.parse.quote(namespace, safe=""))
    segments.append(urllib.parse.quote(name, safe=""))
    identity = "/".join(segments) + "@" + urllib.parse.quote(version, safe="")
    if qualifiers:
        identity += "?" + "&".join(f"{key}={urllib.parse.quote(value, safe=':')}"
                                   for key, value in sorted(qualifiers.items()))
    return identity


def sbom_component(kind: str, name: str, version: str, reference: str,
                   place: tuple[str, str], **fields: object) -> dict[str, object]:
    """One CycloneDX component, with how the bundle relates to it.

    ``place`` is ``(SHIPPED_IN, <bundle path>)`` when the component is present
    in the bundle as files, or ``(BUILD_INPUT, <lock → output>)`` when a
    lockfile pinned it for a build whose output ships. The second is a weaker
    claim on purpose; see :func:`sbom_document`.
    """
    component: dict[str, object] = {"type": kind, "bom-ref": reference, "name": name, "version": version}
    if reference.startswith("pkg:"):
        component["purl"] = reference
    component.update(fields)
    component["properties"] = [{"name": place[0], "value": place[1]}]
    return component


def merged_components(components: Iterable[dict[str, object]]) -> list[dict[str, object]]:
    """One entry per identity; a package used twice keeps both relationships."""
    merged: dict[str, dict[str, object]] = {}
    places: dict[str, set[tuple[str, str]]] = {}
    for component in components:
        reference = str(component["bom-ref"])
        rows: list[dict[str, str]] = component["properties"]  # type: ignore[assignment]
        places.setdefault(reference, set()).update((row["name"], row["value"]) for row in rows)
        merged.setdefault(reference, component)
    for reference, component in merged.items():
        component["properties"] = [{"name": name, "value": value}
                                   for name, value in sorted(places[reference])]
    return [merged[reference] for reference in sorted(merged)]


def python_components(bundle: Path) -> list[dict[str, object]]:
    """Every distribution actually installed in the shipped Python runtime.

    This is the same reading of the same site-packages that writes
    ``_runtime/requirements-lock.txt``, so the two cannot drift apart.
    """
    components = []
    for distribution in importlib.metadata.distributions(path=[str(bundle / PYTHON_SITE)]):
        metadata = distribution.metadata
        name, version = metadata["Name"], distribution.version
        fields: dict[str, object] = {}
        expression, declared = metadata.get("License-Expression"), metadata.get("License")
        if expression:
            # PEP 639 requires this field to be a valid SPDX expression.
            fields["licenses"] = [{"expression": expression}]
        elif declared and "\n" not in declared and len(declared) <= 120:
            # Legacy free text: record it as a name, claiming no SPDX identity.
            fields["licenses"] = [{"license": {"name": declared.strip()}}]
        # purl's pypi rules: lowercase the name and replace _ with -; its own
        # test data canonicalises pkg:PYPI/Django_package to django-package.
        identity = purl("pypi", name.lower().replace("_", "-"), version)
        components.append(sbom_component(
            "library", name, version, identity, (SHIPPED_IN, PYTHON_SITE), **fields))
    return components


def node_components(source: Path, bundle: Path) -> list[dict[str, object]]:
    """Node packages shipped as files, and those a frontend build consumed.

    The adapter tree is read from the bundle itself, so those packages are
    present as shipped files. A frontend's production dependencies are read
    from the tree its ``dist`` was built from: the bundler decides which of
    them reach the output, so they are recorded as build inputs.
    """
    components = []
    for locked, tree, place in (
        ("apps/monkeyhub", bundle / "apps/monkeyhub", (SHIPPED_IN, "apps/monkeyhub/node_modules")),
        ("apps/monkeyhub/web", source / "apps/monkeyhub/web",
         (BUILD_INPUT, "apps/monkeyhub/web/package-lock.json -> apps/monkeyhub/web/dist")),
    ):
        for _, package in installed_node_packages(source / locked, tree, locked):
            name, version = package["name"], package["version"]
            namespace, _, bare = name.rpartition("/")
            fields: dict[str, object] = {}
            declared = package.get("license")
            if isinstance(declared, str) and declared:
                # npm license strings are frequently not valid SPDX expressions.
                fields["licenses"] = [{"license": {"name": declared}}]
            identity = purl("npm", bare, version, namespace=namespace)
            components.append(sbom_component("library", name, version, identity, place, **fields))
    return components


def rust_components(bundle: Path, desktop: dict[str, str]) -> list[dict[str, object]]:
    """The built executable, and the crates its shipped Cargo lock pins.

    Cargo resolves one lock for every target, feature and build script, so a
    locked crate is a build input rather than proof of compiled-in code. Only
    the executable itself is recorded as shipped.
    """
    locked = tomllib.loads((bundle / "_runtime/desktop-Cargo.lock").read_text(encoding="utf-8"))
    components = [sbom_component(
        "application", "MonkeyHub.exe", desktop["version"], "monkeyhub:MonkeyHub.exe",
        (SHIPPED_IN, "MonkeyHub.exe"),
        hashes=[{"alg": "SHA-256", "content": desktop["executableSha256"]}])]
    place = (BUILD_INPUT, "_runtime/desktop-Cargo.lock -> MonkeyHub.exe")
    for crate in locked.get("package", ()):
        if not str(crate.get("source", "")).startswith(CRATES_IO):
            continue  # The local desktop crate ships as MonkeyHub.exe itself.
        fields: dict[str, object] = {}
        if isinstance(crate.get("checksum"), str):
            fields["hashes"] = [{"alg": "SHA-256", "content": crate["checksum"]}]
        identity = purl("cargo", crate["name"], crate["version"])
        components.append(sbom_component(
            "library", crate["name"], crate["version"], identity, place, **fields))
    return components


def sbom_document(source: Path, bundle: Path, build_info: dict, version: str) -> dict[str, object]:
    """A CycloneDX 1.6 bill of materials for this assembled bundle.

    Components are read from the bundle, or from the installed tree a shipped
    build output was produced from, and each says which of the two it is:
    ``monkeyhub:shippedIn`` names a bundle path that carries the component as
    files; ``monkeyhub:buildInput`` names a lockfile that pinned it for a build
    whose output ships, without asserting the component reached that output.
    Runtime versions come from ``build_info``, which stays the single build
    identity.

    The document adds no build-time variation of its own -- no timestamp, no
    serial number -- so the same resolved inventory yields the same bytes. That
    is not a reproducibility guarantee for a source commit: several packaged
    Python requirements are version ranges that pip resolves when the build
    runs, and the Node version comes from the build machine, so two builds of
    one commit can legitimately ship different contents. Reading the installed
    tree rather than the requirement files is what keeps this document true to
    whichever inventory was actually produced.
    """
    inventory = build_info["runtimeInventory"]
    node_version = inventory["nodeVersion"]
    components = [
        sbom_component(
            "application", "CPython", build_info["pythonVersion"],
            purl("generic", "python", build_info["pythonVersion"], qualifiers={
                "download_url": build_info["pythonUrl"],
                "checksum": f"sha256:{build_info['pythonSha256']}"}),
            (SHIPPED_IN, "_runtime/python"),
            externalReferences=[{"type": "distribution", "url": build_info["pythonUrl"], "hashes": [
                {"alg": "SHA-256", "content": build_info["pythonSha256"]}]}]),
        sbom_component(
            "application", "Node.js", node_version,
            purl("generic", "node", node_version), (SHIPPED_IN, "_runtime/node/node.exe"),
            hashes=[{"alg": "SHA-256", "content": sha256(bundle / "_runtime/node/node.exe")}],
            externalReferences=[{"type": "distribution", "url": f"https://nodejs.org/dist/{node_version}/"}]),
        *python_components(bundle),
        *node_components(source, bundle),
    ]
    if "desktop" in build_info:
        components.extend(rust_components(bundle, build_info["desktop"]))
    components = merged_components(components)
    return {
        "bomFormat": "CycloneDX",
        "specVersion": SBOM_SPEC_VERSION,
        "version": 1,
        "metadata": {
            "component": {
                "type": "application", "bom-ref": "monkeyhub", "name": "MonkeyHub", "version": version,
                "description": "MonkeyHub Windows candidate bundle: Hub, Studio workspaces, "
                               "MonkeyMonitor and MonkeyFab from one Hub source commit.",
            },
            "tools": {"components": [{"type": "application", "name": "package_monkeyapps",
                                      "version": build_info["sourceCommit"]}]},
            "properties": [
                {"name": "monkeyhub:sourceCommit", "value": build_info["sourceCommit"]},
                {"name": "monkeyhub:target", "value": build_info["target"]},
                {"name": "monkeyhub:channel", "value": build_info["channel"]},
                {"name": f"{SHIPPED_IN}:meaning",
                 "value": "Bundle-relative location that carries this component as shipped files."},
                {"name": f"{BUILD_INPUT}:meaning",
                 "value": "A lockfile that pinned this component for the named build output. The "
                          "component is not asserted to be present in that output: a frontend bundler "
                          "includes only what it reaches, and one Cargo lock covers every target, "
                          "feature and build script."},
            ],
        },
        "components": components,
        "dependencies": [{"ref": "monkeyhub", "dependsOn": [component["bom-ref"] for component in components]}],
    }


def artifact_facts(path: Path) -> dict[str, object]:
    return {"path": path.name, "size": path.stat().st_size, "sha256": sha256(path)}


def release_manifest(build_info: dict, version: str, prefix: str, build_info_path: Path,
                     archive: Path, artifacts: Iterable[Path], sbom: Path) -> dict[str, object]:
    """A derived view of ``build-info.json`` closed over the distributed files.

    This mints no version of its own: every release fact is read from the
    build metadata already written into the bundle. ``buildInfo`` and ``sbom``
    name their path inside the archive as well as their digest, so
    :func:`verify_release` can open the archive and check those copies instead
    of only restating a hash. Nothing here is signed, and the trust block says
    so rather than implying that a checksum establishes origin.
    """
    inventory = build_info["runtimeInventory"]
    return {
        "schema": RELEASE_MANIFEST_SCHEMA,
        "release": {
            "version": version,
            "channel": build_info["channel"],
            "target": build_info["target"],
            "sourceCommit": build_info["sourceCommit"],
        },
        "trust": {
            "status": f"{build_info['channel']}-unsigned",
            "signed": False,
            "signature": None,
            "statement": "This release is not signed. The SHA-256 values below detect a changed file "
                         "for a manifest obtained over a trusted channel; they do not establish origin. "
                         "Do not treat this build as production-trusted.",
        },
        "archive": archive.name,
        "buildInfo": {
            "pathInArchive": f"{prefix}/{build_info_path.name}",
            "sha256": sha256(build_info_path),
        },
        "build": {
            "pythonVersion": build_info["pythonVersion"],
            "pythonUrl": build_info["pythonUrl"],
            "pythonSha256": build_info["pythonSha256"],
            "nodeVersion": inventory["nodeVersion"],
            "acpAdapter": inventory["acpAdapter"],
            "pythonRequirements": inventory["pythonRequirements"],
            **({"desktop": build_info["desktop"]} if "desktop" in build_info else {}),
        },
        "sbom": {
            "format": "CycloneDX", "specVersion": SBOM_SPEC_VERSION,
            "path": sbom.name, "pathInArchive": f"{prefix}/{SBOM_NAME}",
            "sha256": sha256(sbom),
        },
        "artifactPrefix": prefix,
        "artifacts": sorted((artifact_facts(path) for path in artifacts), key=lambda row: row["path"]),
    }


def unsafe_name(value: object) -> str | None:
    """Why this is not one plain filename, or None when it is.

    A manifest may have been written on another platform, so a separator is
    rejected by character rather than by asking this platform to parse the
    string: ``..\\escape`` is one harmless filename to posixpath and an escape
    on Windows, and the reverse holds for ``../escape``.
    """
    if not isinstance(value, str) or not value:
        return "must be a non-empty name"
    if value in (".", ".."):
        return "must not be a relative directory name"
    if re.search(r'[\\/:*?"<>|]|[\x00-\x1f]', value):
        return "must not contain a path separator, drive letter or reserved character"
    if value != value.strip() or value.endswith("."):
        return "must not have surrounding whitespace or a trailing dot"
    return None


def archive_digests(archive: Path, wanted: Iterable[str]) -> tuple[dict[str, str], list[str]]:
    """SHA-256 of named members of a distributed archive, plus what went wrong."""
    digests: dict[str, str] = {}
    problems: list[str] = []
    try:
        with zipfile.ZipFile(archive) as opened:
            for name in wanted:
                try:
                    entry = opened.getinfo(name)
                except KeyError:
                    problems.append(f"{archive.name}: does not contain {name}")
                    continue
                if entry.file_size > 64 * 1024 * 1024:
                    problems.append(f"{archive.name}: {name} is {entry.file_size} bytes, too large to be release metadata")
                    continue
                digests[name] = hashlib.sha256(opened.read(name)).hexdigest()
    except (OSError, zipfile.BadZipFile, RuntimeError) as error:
        problems.append(f"{archive.name}: unreadable archive: {error}")
    return digests, problems


def verify_release(manifest: Path) -> list[str]:
    """Check a release directory against its closed manifest; report every miss.

    Three bindings are checked, not one. Every distributed file must match the
    size and SHA-256 in the closed table, and nothing carrying this release's
    name may sit beside it unlisted. The archive is then opened so that the
    ``build-info.json`` and SBOM copies travelling inside it are hashed against
    the digests the manifest records, and the SBOM sidecar must agree with that
    same digest. This proves the files match this manifest. It cannot prove the
    manifest is authentic: an unsigned manifest carries no origin, so a reader
    who obtained it from an untrusted place learns nothing about who built it.
    """
    try:
        document = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        return [f"{manifest.name}: unreadable release manifest: {error}"]
    if not isinstance(document, dict) or document.get("schema") != RELEASE_MANIFEST_SCHEMA:
        return [f"{manifest.name}: not a {RELEASE_MANIFEST_SCHEMA} document"]
    prefix = document.get("artifactPrefix")
    fault = unsafe_name(prefix)
    if fault:
        return [f"{manifest.name}: artifactPrefix {fault}"]
    artifacts = document.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        return [f"{manifest.name}: the manifest lists no distributed artifact"]
    directory = manifest.parent
    problems: list[str] = []
    listed: dict[str, dict] = {}
    for entry in artifacts:
        # A downloaded manifest is untrusted input; report it, never trip over it.
        if not isinstance(entry, dict):
            problems.append(f"{entry!r}: an artifact entry must be an object")
            continue
        name = entry.get("path")
        fault = unsafe_name(name)
        if fault:
            problems.append(f"{name!r}: an artifact path {fault}")
            continue
        name = str(name)
        if name in listed:
            problems.append(f"{name}: listed twice; a closed table names each file once")
            continue
        if not name.startswith(str(prefix)):
            problems.append(f"{name}: does not carry the release prefix {prefix!r}, so the closed set cannot cover it")
            continue
        listed[name] = entry
        artifact = directory / name
        if not artifact.is_file():
            problems.append(f"{name}: listed in the manifest but not present")
            continue
        size = artifact.stat().st_size
        if size != entry.get("size"):
            problems.append(f"{name}: {size} bytes on disk, manifest records {entry.get('size')}")
        digest = sha256(artifact)
        if digest != entry.get("sha256"):
            problems.append(f"{name}: SHA-256 {digest} on disk, manifest records {entry.get('sha256')}")
    # One output directory holds several releases, so the closed set is the
    # files carrying this release's own name.
    for path in sorted(directory.iterdir()):
        if path.is_file() and path.name.startswith(str(prefix)) and path.name not in listed \
                and path.name != manifest.name:
            problems.append(f"{path.name}: distributed beside this release but absent from its closed manifest")
    problems.extend(verify_bound_metadata(directory, document, listed))
    return problems


def verify_bound_metadata(directory: Path, document: dict, listed: dict[str, dict]) -> list[str]:
    """Check the build metadata and SBOM digests the manifest claims to bind."""
    problems: list[str] = []
    sbom = document.get("sbom") if isinstance(document.get("sbom"), dict) else {}
    build_info = document.get("buildInfo") if isinstance(document.get("buildInfo"), dict) else {}
    sidecar = sbom.get("path")
    if unsafe_name(sidecar) or str(sidecar) not in listed:
        problems.append("the SBOM sidecar is not one of the listed artifacts")
    elif sbom.get("sha256") != listed[str(sidecar)].get("sha256"):
        # The artifact table and the SBOM block must not disagree about one file.
        problems.append(f"{sidecar}: the artifact table records "
                        f"{listed[str(sidecar)].get('sha256')} but the sbom block records {sbom.get('sha256')}")
    archive_name = document.get("archive")
    if unsafe_name(archive_name) or str(archive_name) not in listed:
        return problems + ["the distributed archive is not one of the listed artifacts"]
    archive = directory / str(archive_name)
    if not archive.is_file():
        return problems  # Already reported as a missing listed artifact.
    wanted = {}
    for label, block in (("build-info.json", build_info), ("SBOM", sbom)):
        member = block.get("pathInArchive")
        if not isinstance(member, str) or not member or ".." in member.split("/"):
            problems.append(f"{label}: pathInArchive must be a path inside the archive")
            continue
        if not member.startswith(f"{document['artifactPrefix']}/"):
            problems.append(f"{label}: {member} is not inside {document['artifactPrefix']}/")
            continue
        expected_member = f"{document['artifactPrefix']}/{SBOM_NAME if label == 'SBOM' else label}"
        if member != expected_member:
            problems.append(f"{label}: pathInArchive must name {expected_member}")
            continue
        wanted[member] = (label, block.get("sha256"))
    if not wanted:
        return problems
    digests, failures = archive_digests(archive, wanted)
    problems.extend(failures)
    for member, (label, expected) in wanted.items():
        if member in digests and digests[member] != expected:
            problems.append(f"{member}: SHA-256 {digests[member]} inside {archive.name}, "
                            f"manifest binds {label} to {expected}")
    return problems


def package(source_root: Path, source_ref: str, staging: Path, output: Path,
            cache: Path, node: Path, npm_cli: Path,
            *, desktop: bool = False, cargo: Path | None = None) -> Path:
    if sys.platform != "win32":
        raise ValueError("Build and verify this Windows candidate on Windows x64.")
    source_root = source_root.resolve()
    staging, output, cache = (external(path, source_root) for path in (staging, output, cache))
    commit = run(["git", "rev-parse", "--verify", f"{source_ref}^{{commit}}"], cwd=source_root, capture=True)
    version = commit[:12]
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
    build_web(source, node, npm_cli, environment)
    bundle = build / f"MonkeyHub-{version}-windows-x64"
    collect_application(source, bundle, commit, node=node)
    prepare_runtime(source, bundle / "_runtime/python", cache, environment)
    smoke_runtime(bundle)
    desktop_info = build_desktop(source, bundle, commit, cargo, environment) if desktop else None
    # Version + exact inputs are distribution metadata, not project records.
    build_info = {
        "sourceCommit": commit, "target": "windows-x64", "channel": "candidate",
        **({"desktop": desktop_info} if desktop_info else {}),
        "pythonVersion": PYTHON_VERSION, "pythonUrl": PYTHON_URL, "pythonSha256": PYTHON_SHA256,
        "runtimeInventory": runtime_inventory(source, bundle),
    }
    build_info_path = bundle / "build-info.json"
    build_info_path.write_text(json.dumps(build_info, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    # The bill of materials ships inside the archive and again beside it, so a
    # reader can inspect the dependencies without unpacking the candidate.
    (bundle / SBOM_NAME).write_text(json.dumps(
        sbom_document(source, bundle, build_info, version), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    zip_path = build / f"{bundle.name}-candidate.zip"
    with zipfile.ZipFile(zip_path, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(bundle.rglob("*")):
            if path.is_file():
                archive.write(path, (Path(bundle.name) / path.relative_to(bundle)).as_posix())
    # The archive is finalized on the staging drive before consuming output space.
    if shutil.disk_usage(output).free < zip_path.stat().st_size + 64 * 1024 * 1024:
        raise ValueError(f"Not enough free space for the completed ZIP in {output}; it remains at {zip_path}")
    final = output / zip_path.name
    checksum = output / f"{final.name}.sha256"
    sbom = output / f"{bundle.name}.cyclonedx.json"
    manifest = output / f"{final.name}.release-manifest.json"
    for existing in (final, checksum, sbom, manifest):
        if existing.exists():
            raise ValueError(f"A release with this source name already exists; choose another output directory: {existing}")
    shutil.copy2(zip_path, final)
    checksum.write_text(f"{sha256(final)}  {final.name}\n", encoding="utf-8")
    shutil.copy2(bundle / SBOM_NAME, sbom)
    manifest.write_text(json.dumps(release_manifest(
        build_info, version, bundle.name, build_info_path, final, (final, checksum, sbom), sbom),
        ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    problems = verify_release(manifest)
    if problems:
        raise ValueError("The finished release does not match its own manifest: " + "; ".join(problems))
    print(f"Candidate: {final}\nBytes: {final.stat().st_size}\nUnpacked: {bundle}\n"
          f"Release manifest: {manifest} (trust: candidate-unsigned)\nSBOM: {sbom}", flush=True)
    return final


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=SOURCE_ROOT)
    parser.add_argument("--source-ref", default="HEAD", help="exact integrated commit or ref; working files are not packaged")
    parser.add_argument("--workspace-root", type=Path,
                        help=f"external build root; defaults to Git config {WORKSPACE_CONFIG_KEY}")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--configure", action="store_true",
                        help="save --workspace-root in personal Git config and show paths without building")
    action.add_argument("--show-paths", action="store_true", help="show resolved paths without creating directories or building")
    action.add_argument("--verify", type=Path, metavar="RELEASE_MANIFEST",
                        help="check the distributed files beside a ReleaseManifest@1 and exit; builds nothing")
    action.add_argument("--patch-from", type=Path, metavar="BASE_DIRECTORY",
                        help="build a local developer delta from two complete desktop bundles; builds no runtimes")
    parser.add_argument("--patch-to", type=Path, metavar="TARGET_DIRECTORY")
    parser.add_argument("--patch-output", type=Path, metavar="PATCH_ZIP")
    parser.add_argument("--task", help="task directory name; defaults to the current branch")
    parser.add_argument("--staging-dir", type=Path, help="overrides <workspace-root>/temp/package-monkeyapps/<task>")
    parser.add_argument("--output-dir", type=Path, help="overrides <workspace-root>/packages/<task>")
    parser.add_argument("--cache-dir", type=Path,
                        help="overrides <workspace-root>/cache/package-monkeyapps; otherwise defaults to <staging-dir>/cache")
    parser.add_argument("--node", type=Path, default=Path(shutil.which("node") or "node.exe"))
    parser.add_argument("--npm-cli", type=Path, help="path to npm/bin/npm-cli.js; no shell or npm.cmd interpolation")
    parser.add_argument("--desktop", action="store_true", help="build MonkeyHub.exe from the same snapshot using Rust/MSVC")
    parser.add_argument("--cargo", type=Path, default=Path(shutil.which("cargo") or "cargo.exe"),
                        help="Cargo executable for --desktop; build dependencies stay outside the source checkout")
    args = parser.parse_args(argv)
    npm_cli = args.npm_cli or args.node.resolve().parent / "node_modules/npm/bin/npm-cli.js"
    try:
        if args.patch_from is not None:
            if args.patch_to is None or args.patch_output is None:
                raise ValueError("--patch-from requires --patch-to and --patch-output.")
            patch_output = external(args.patch_output, args.source_root.resolve())
            result = create_patch(args.patch_from, args.patch_to, patch_output)
            print(json.dumps({"patch": str(patch_output), **result}, ensure_ascii=False, indent=2))
            return 0
        if args.patch_to is not None or args.patch_output is not None:
            raise ValueError("--patch-to and --patch-output require --patch-from.")
        if args.verify is not None:
            problems = verify_release(args.verify.resolve())
            for problem in problems:
                print(problem)
            print("Release verification: " + ("FAIL" if problems else
                  "PASS (files match this manifest; the manifest itself is unsigned)"), flush=True)
            return 1 if problems else 0
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
                desktop=args.desktop, cargo=args.cargo)
    except (OSError, ValueError, subprocess.CalledProcessError, zipfile.BadZipFile) as error:
        parser.exit(1, f"package_monkeyapps: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
