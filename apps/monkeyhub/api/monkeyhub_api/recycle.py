"""Putting away a folder the Hub made, never deleting it for good.

The Hub removes nothing permanently: what it no longer needs goes to the
Windows Recycle Bin, where the person can still get it back and empties it
themselves. In two steps, so the slow one never holds a caller's lock:

- ``set_aside`` renames the folder into a quarantine folder on the same volume
  (``<runtime root>/_TRASH_<YYYYMMDD>``). A rename on one volume is immediate and
  loses nothing; the place it was is free again at once.
- ``recycle`` then hands that folder to the Recycle Bin, but only when the bin
  will really keep it. Windows silently deletes for good what its bin cannot
  hold: a path too long for the bin, a folder larger than the bin's size, a
  volume whose bin is turned off or a drive that has none. Each of those is
  checked first, and a folder that fails any check stays in quarantine for the
  person to delete, as the user's own recycle script does.

Elsewhere than Windows nothing is recycled: the folder stays in quarantine.
"""

from __future__ import annotations

from datetime import datetime
import logging
import os
from pathlib import Path
import secrets
import threading

_log = logging.getLogger(__name__)

# The user's recycle script refuses a tree with a path of 240 characters or more.
_PATH_LIMIT = 240
# What the bin puts in front of a folder's own relative paths: C:\$Recycle.Bin\<SID>\$R<six>\ (about 70 characters).
_BIN_PREFIX = 80
# Only this share of the bin's configured size is used for one folder, so the bin never has to evict to take it.
_BIN_SHARE = 0.5


def quarantine_dir(runtime_root: Path, now: datetime | None = None) -> Path:
    """This day's quarantine folder under the runtime root; the same volume as a chat's scratch."""
    return Path(runtime_root) / f"_TRASH_{(now or datetime.now()).strftime('%Y%m%d')}"


def set_aside(path: Path, runtime_root: Path, label: str) -> Path | None:
    """Rename ``path`` into quarantine and answer where it went.

    None when there was nothing there, or when it could not be renamed (a file
    in it still open, another volume): then it stays exactly where it was, and
    nothing in it is removed.
    """
    path = Path(path)
    if not path.exists():
        return None
    target_root = quarantine_dir(runtime_root)
    target = target_root / f"{label}-{datetime.now().strftime('%H%M%S')}-{secrets.token_hex(3)}"
    try:
        target_root.mkdir(parents=True, exist_ok=True)
        os.replace(path, target)
    except OSError as failure:
        _log.warning("Could not set %s aside; it stays where it is: %s", path, failure)
        return None
    return target


def recycle(path: Path) -> bool:
    """Send one quarantined folder to the Recycle Bin when the bin will keep it whole.

    True only when the bin took it. Any refusal leaves it in quarantine.
    """
    path = Path(path)
    if os.name != "nt" or not path.exists():
        return False
    if _under_temp(path):
        # A throwaway runtime (tests, a trial run) keeps its quarantine with it rather than filling the person's bin.
        return False
    try:
        reason = _not_recyclable(path)
    except OSError as failure:
        reason = f"could not be measured ({failure})"
    if reason:
        _log.info("%s stays in quarantine: %s", path, reason)
        return False
    return _shell_recycle(path)


def discard(path: Path, runtime_root: Path, label: str, *, wait: bool = False) -> Path | None:
    """Set ``path`` aside now and recycle it afterwards; answers the quarantined path, or None.

    The recycling runs on a thread of its own unless ``wait``, so a caller
    holding a lock only pays for the rename.
    """
    aside = set_aside(path, runtime_root, label)
    if aside is None:
        return None
    if wait:
        recycle(aside)
    else:
        threading.Thread(target=recycle, args=(aside,), daemon=True, name="hub-recycle").start()
    return aside


def _under_temp(path: Path) -> bool:
    import tempfile

    try:
        return path.resolve().is_relative_to(Path(tempfile.gettempdir()).resolve())
    except OSError:
        return False


def _not_recyclable(path: Path) -> str | None:
    """Why the Recycle Bin would not keep this folder whole, or None when it would."""
    import ctypes

    text = str(path.resolve())
    if text.startswith("\\\\"):
        return "it is not on a lettered drive"
    drive = text[:3]
    if ctypes.windll.kernel32.GetDriveTypeW(drive) != 3:  # DRIVE_FIXED
        return "its drive keeps no Recycle Bin"
    capacity = _bin_capacity(drive)
    if capacity is None:
        return "the Recycle Bin settings of its volume could not be read, or the bin is turned off"
    total = 0
    if len(text) >= _PATH_LIMIT:
        return "its path is too long for the Recycle Bin"
    for folder, names, files in os.walk(text):
        for name in (*names, *files):
            full = os.path.join(folder, name)
            relative = len(full) - len(text)
            if len(full) >= _PATH_LIMIT or relative + _BIN_PREFIX >= _PATH_LIMIT:
                return "a path in it is too long for the Recycle Bin"
        for name in files:
            try:
                total += os.lstat(os.path.join(folder, name)).st_size
            except OSError:
                pass
    if total > capacity * _BIN_SHARE:
        return "it is larger than the Recycle Bin keeps"
    return None


def _bin_capacity(drive: str) -> int | None:
    """The bin's size in bytes for the volume of ``drive``, or None when it is off or unknown."""
    import ctypes
    import winreg

    buffer = ctypes.create_unicode_buffer(64)
    if not ctypes.windll.kernel32.GetVolumeNameForVolumeMountPointW(drive, buffer, len(buffer)):
        return None
    name = buffer.value  # \\?\Volume{GUID}\
    start, end = name.find("{"), name.find("}")
    if start < 0 or end < start:
        return None
    key = r"Software\Microsoft\Windows\CurrentVersion\Explorer\BitBucket\Volume" + "\\" + name[start:end + 1]
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as handle:
            nuke = winreg.QueryValueEx(handle, "NukeOnDelete")[0]
            megabytes = winreg.QueryValueEx(handle, "MaxCapacity")[0]
    except OSError:
        return None
    if nuke or not isinstance(megabytes, int) or megabytes <= 0:
        return None
    return megabytes * 1024 * 1024


def _shell_recycle(path: Path) -> bool:
    """The shell's own delete with undo: what Explorer's Delete does, with no window."""
    import ctypes
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", ctypes.c_void_p),
                    ("pTo", ctypes.c_void_p), ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", wintypes.BOOL),
                    ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]

    fo_delete = 3
    # ALLOWUNDO sends it to the bin; WANTNUKEWARNING makes the shell ask rather than quietly delete for good,
    # should a check above ever miss a case; SILENT, NOCONFIRMATION and NOERRORUI keep it windowless otherwise.
    flags = 0x0040 | 0x4000 | 0x0004 | 0x0010 | 0x0400
    source = ctypes.create_unicode_buffer(str(path.resolve()) + "\0")  # double-null terminated
    operation = SHFILEOPSTRUCTW(None, fo_delete, ctypes.cast(source, ctypes.c_void_p), None, flags, False, None, None)
    try:
        result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(operation))
    except OSError as failure:
        _log.warning("The Recycle Bin refused %s: %s", path, failure)
        return False
    if result != 0 or operation.fAnyOperationsAborted or path.exists():
        _log.warning("The Recycle Bin did not take %s (code %s); it stays in quarantine.", path, result)
        return False
    return True
