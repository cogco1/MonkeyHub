"""Read-only installation evidence. Discovery never grants execution capability."""
from dataclasses import dataclass
import os
from pathlib import Path
import platform
import re
import shutil


_WINDOWS_LOCATIONS = {
    "rhino": tuple(f"Rhino {version}/System/Rhino.exe" for version in range(9, 5, -1)),
    "blender": ("Blender Foundation/Blender */blender.exe", "Blender Foundation/Blender/blender.exe"),
    "sketchup": ("SketchUp/SketchUp */SketchUp.exe", "SketchUp/SketchUp */SketchUp/SketchUp.exe"),
    "autocad": ("Autodesk/AutoCAD */acad.exe",),
    "autocad-core": ("Autodesk/AutoCAD */accoreconsole.exe",),
}
_MAC_LOCATIONS = {
    "rhino": ("Rhino *.app/Contents/MacOS/Rhinoceros",),
    "blender": ("Blender*.app/Contents/MacOS/Blender",),
    "sketchup": ("SketchUp */SketchUp.app/Contents/MacOS/SketchUp",),
    "autocad": ("Autodesk/AutoCAD */AutoCAD *.app/Contents/MacOS/AutoCAD",),
}
_APP_EXECUTABLES = {
    "rhino": "Rhino.exe", "blender": "blender.exe", "sketchup": "SketchUp.exe",
    "autocad": "acad.exe", "autocad-core": "accoreconsole.exe",
}
_VERSION_PATTERNS = {
    "rhino": r"Rhino\s+(\d+(?:\.\d+)*)",
    "blender": r"Blender\s+(\d+(?:\.\d+)*)",
    "sketchup": r"SketchUp\s+(20\d{2})",
    "autocad": r"AutoCAD\s+(20\d{2})",
    "autocad-core": r"AutoCAD\s+(20\d{2})",
}


@dataclass(frozen=True)
class Installation:
    product: str
    executable: Path
    version_hint: str | None
    evidence: str
    architecture: str = "unknown"

    @property
    def product_id(self):
        return self.product

    def public(self):
        # Machine paths are local discovery details, never project identities.
        return {"product": self.product, "productId": self.product_id,
                "executableName": self.executable.name, "architecture": self.architecture,
                "version": None, "versionHint": self.version_hint,
                "versionVerified": False, "evidence": self.evidence}


@dataclass(frozen=True)
class Discovery:
    system: str
    installations: tuple[Installation, ...]
    diagnostics: tuple[str, ...] = ()


def _app_paths(diagnostics, products):
    """Only Windows App Paths for requested products; no process invocation."""
    import winreg
    found = []
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            for product in products:
                executable = _APP_EXECUTABLES[product]
                key_name = "SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\App Paths\\" + executable
                try:
                    with winreg.OpenKey(hive, key_name, 0, winreg.KEY_READ | view) as key:
                        value, kind = winreg.QueryValueEx(key, "")
                    if kind in (winreg.REG_SZ, winreg.REG_EXPAND_SZ) and isinstance(value, str):
                        path = Path(os.path.expandvars(value.strip().strip('"')))
                        if path.is_absolute() and path.name.lower() == executable.lower():
                            found.append((product, path, "windows-app-path"))
                except FileNotFoundError:
                    continue
                except OSError:
                    diagnostics.append("Some Windows App Paths could not be read; discovery is incomplete.")
    return found


class SoftwareDiscoveryRegistry:
    """Bounded, read-only installation evidence for the named local products.

    Roots and candidates are injectable for deterministic host integration. No
    recursive search, shell, DLL loading, network, launch or project write occurs.
    A directory version is a hint, never verified host compatibility.
    """

    def __init__(self, *, system=None, program_roots=None, application_roots=None,
                 registry_candidates=None, path_candidates=None):
        self.system = system or platform.system()
        self.program_roots = None if program_roots is None else tuple(program_roots)
        self.application_roots = None if application_roots is None else tuple(application_roots)
        self.registry_candidates = None if registry_candidates is None else tuple(registry_candidates)
        self.path_candidates = None if path_candidates is None else tuple(path_candidates)

    def discover(self, product_id):
        if not isinstance(product_id, str) or product_id not in _WINDOWS_LOCATIONS:
            raise ValueError(f"Unknown software product: {product_id!r}")
        return self._discover((product_id,))

    def _discover(self, products):
        candidates, diagnostics = [], []
        roots, locations, evidence = (), {}, "standard-install-directory"
        if self.system == "Windows":
            roots = self.program_roots if self.program_roots is not None else tuple(dict.fromkeys(
                Path(os.environ.get(key, default)) for key, default in (
                    ("ProgramFiles", r"C:\Program Files"), ("ProgramFiles(x86)", r"C:\Program Files (x86)"))))
            locations = _WINDOWS_LOCATIONS
        elif self.system == "Darwin":
            roots = self.application_roots if self.application_roots is not None else (
                Path("/Applications"), Path.home() / "Applications")
            locations, evidence = _MAC_LOCATIONS, "application-bundle"
        elif self.system != "Linux" or "blender" not in products:
            diagnostics.append("Native discovery has no supported desktop locations on this operating system.")
        for root in roots:
            for product in products:
                for pattern in locations.get(product, ()):
                    try:
                        candidates.extend((product, path, evidence) for path in Path(root).glob(pattern))
                    except OSError:
                        diagnostics.append("A standard installation directory could not be inspected.")
        if self.system == "Windows":
            registered = self.registry_candidates
            if registered is None:
                # Do not import Windows-only bindings on another host platform.
                registered = _app_paths(diagnostics, products) if os.name == "nt" else ()
            candidates.extend(row for row in registered if row[0] in products)
        if "blender" in products and self.system in ("Windows", "Darwin", "Linux"):
            paths = self.path_candidates
            if paths is None:
                command = shutil.which("blender")
                paths = (command,) if command else ()
            # PATH takes precedence, matching existing Blender selection. Symlink
            # commands, common on Unix PATH, are compared by target but kept as
            # named: launchers such as snap dispatch on argv[0].
            candidates[:0] = [("blender", path, "path") for path in paths]
        found = {}
        for product, path, source in candidates:
            path = Path(path)
            try:
                if source == "path":
                    path = Path(os.path.abspath(path))
                if (not path.is_absolute() or not path.is_file() or
                        (path.is_symlink() and source != "path")):
                    continue
                resolved = path.resolve()
            except OSError:
                diagnostics.append("An installation candidate could not be inspected.")
                continue
            hint = re.search(_VERSION_PATTERNS[product], str(path), re.I)
            key = product, str(resolved)
            if key not in found or found[key].evidence != "path":
                executable = path if source == "path" else resolved
                found[key] = Installation(product, executable, hint.group(1) if hint else None, source)
        return Discovery(self.system, tuple(sorted(found.values(), key=_preference)),
                         tuple(dict.fromkeys(diagnostics)))


def _preference(installation):
    version = tuple(-int(part) for part in (installation.version_hint or "0").split("."))
    version += (0,) * (4 - len(version))
    return installation.product, installation.evidence != "path", version, str(installation.executable)


def resolve_blender_executable(selected=None):
    """An explicit executable/command wins; automatic lookup also uses known installs."""
    if selected is not None:
        return shutil.which(str(selected))
    installations = SoftwareDiscoveryRegistry().discover("blender").installations
    return str(installations[0].executable) if installations else None


def discover_local_cad(*, system=None, program_roots=None, application_roots=None,
                       registry_candidates=None):
    """Compatibility view for the existing SketchUp and AutoCAD providers."""
    registry = SoftwareDiscoveryRegistry(system=system, program_roots=program_roots,
        application_roots=application_roots, registry_candidates=registry_candidates)
    result = registry._discover(("sketchup", "autocad", "autocad-core"))
    return Discovery(result.system, tuple(sorted(result.installations,
        key=lambda row: (row.product, str(row.executable)))), result.diagnostics)
