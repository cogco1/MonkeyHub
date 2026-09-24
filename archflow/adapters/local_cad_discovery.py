"""Read-only installation evidence. Discovery never grants execution capability."""
from dataclasses import dataclass
import os
from pathlib import Path
import platform
import re


@dataclass(frozen=True)
class Installation:
    product: str
    executable: Path
    version_hint: str | None
    evidence: str

    def public(self):
        # Machine paths are local discovery details, never project identities.
        return {"product": self.product, "executableName": self.executable.name,
                "version": None, "versionHint": self.version_hint,
                "versionVerified": False, "evidence": self.evidence}


@dataclass(frozen=True)
class Discovery:
    system: str
    installations: tuple[Installation, ...]
    diagnostics: tuple[str, ...] = ()


def _app_paths(diagnostics):
    """Only Windows App Paths for the three named products; no process invocation."""
    import winreg
    found = []
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            for product, executable in (("sketchup", "SketchUp.exe"), ("autocad", "acad.exe"),
                                        ("autocad-core", "accoreconsole.exe")):
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


def discover_local_cad(*, system=None, program_roots=None, application_roots=None,
                       registry_candidates=None):
    """Bounded known-directory/App Paths lookup, like existing Rhino discovery.

    Injectable roots/evidence support deterministic tests and explicit host
    integration. No recursive disk search, shell, DLL loading, network, licensing
    request, CAD launch, bridge handshake, or project persistence occurs here.
    """
    system = system or platform.system()
    candidates, diagnostics = [], []
    if system == "Windows":
        roots = program_roots if program_roots is not None else tuple(dict.fromkeys(
            Path(os.environ.get(key, default)) for key, default in (
                ("ProgramFiles", r"C:\Program Files"), ("ProgramFiles(x86)", r"C:\Program Files (x86)"))))
        patterns = (("sketchup", "SketchUp/SketchUp */SketchUp.exe"),
                    ("autocad", "Autodesk/AutoCAD */acad.exe"),
                    ("autocad-core", "Autodesk/AutoCAD */accoreconsole.exe"))
        for root in roots:
            for product, pattern in patterns:
                try:
                    candidates.extend((product, path, "standard-install-directory") for path in Path(root).glob(pattern))
                except OSError:
                    diagnostics.append("A standard installation directory could not be inspected.")
        if registry_candidates is None:
            # Do not import Windows-only registry bindings on another platform.
            registry_candidates = _app_paths(diagnostics) if os.name == "nt" else ()
        candidates.extend(registry_candidates)
    elif system == "Darwin":
        roots = application_roots if application_roots is not None else (Path("/Applications"), Path.home()/"Applications")
        for root in roots:
            for product, pattern in (("sketchup", "SketchUp */SketchUp.app/Contents/MacOS/SketchUp"),
                                     ("autocad", "Autodesk/AutoCAD */AutoCAD *.app/Contents/MacOS/AutoCAD")):
                try:
                    candidates.extend((product, path, "application-bundle") for path in Path(root).glob(pattern))
                except OSError:
                    diagnostics.append("An application directory could not be inspected.")
    else:
        diagnostics.append("Native discovery has no supported desktop locations on this operating system.")
    found = {}
    for product, path, evidence in candidates:
        path = Path(path)
        try:
            if not path.is_absolute() or not path.is_file() or path.is_symlink():
                continue
            resolved = path.resolve()
        except OSError:
            diagnostics.append("An installation candidate could not be inspected.")
            continue
        year = re.search(r"(?:SketchUp|AutoCAD)\s+(20\d{2})", str(path), re.I)
        found[(product, str(resolved))] = Installation(product, resolved, year.group(1) if year else None, evidence)
    return Discovery(system, tuple(found[key] for key in sorted(found)), tuple(dict.fromkeys(diagnostics)))
