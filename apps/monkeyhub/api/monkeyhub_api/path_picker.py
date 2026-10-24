"""Windows' own folder and file dialog, for a person who would otherwise type a path.

The Hub runs on the person's computer, so a dialog it asks for opens on their screen. The caller is
checked to be this machine; the dialog is a PowerShell child that is given what to ask in its
environment, never in its script, and prints the chosen path (nothing when the person cancels).
"""

from __future__ import annotations

import base64
import os
import subprocess
import sys
from typing import Callable, Literal

from .models import HubFailure

#: How long a dialog may stay open before it is given up on (the person walked away).
DIALOG_TIMEOUT_S = 600.0

Kind = Literal["folder", "file"]
Runner = Callable[..., subprocess.CompletedProcess]

_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Windows.Forms
[System.Windows.Forms.Application]::EnableVisualStyles()
$owner = New-Object System.Windows.Forms.Form
$owner.TopMost = $true; $owner.ShowInTaskbar = $false; $owner.Opacity = 0
$owner.StartPosition = 'CenterScreen'; $owner.Width = 1; $owner.Height = 1
$owner.Show(); $owner.Activate()
try {
  if ($env:MH_PICK_KIND -eq 'folder') {
    $dialog = New-Object System.Windows.Forms.FolderBrowserDialog
    $dialog.Description = $env:MH_PICK_TITLE
    $dialog.ShowNewFolderButton = $false
    if ($dialog.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK) { [Console]::Out.Write($dialog.SelectedPath) }
  } else {
    $dialog = New-Object System.Windows.Forms.OpenFileDialog
    $dialog.Title = $env:MH_PICK_TITLE
    $dialog.Filter = $env:MH_PICK_FILTER
    $dialog.CheckFileExists = $true
    if ($dialog.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK) { [Console]::Out.Write($dialog.FileName) }
  }
} finally { $owner.Close() }
"""

#: The file kinds a dialog may offer, by name; the caller never supplies a filter string.
FILTERS = {"archive": "Project archive (*.zip)|*.zip|All files (*.*)|*.*", "any": "All files (*.*)|*.*"}


def _run(command, **kwargs) -> subprocess.CompletedProcess:
    # The Hub's own stdin is a pipe the desktop holds; the child gets none (#58).
    return subprocess.run(command, stdin=subprocess.DEVNULL, **kwargs)


def pick(kind: Kind, title: str, *, file_filter: str = "any", runner: Runner | None = None) -> str | None:
    """The path the person chose, or None when they closed the dialog."""

    if sys.platform != "win32" and runner is None:
        raise HubFailure(501, "PATH_PICKER_UNAVAILABLE", "Choosing a path in a dialog is only available on Windows; type the path instead.")
    run = _run if runner is None else runner
    encoded = base64.b64encode(_SCRIPT.encode("utf-16-le")).decode("ascii")
    environment = {**os.environ, "MH_PICK_KIND": kind, "MH_PICK_TITLE": title[:200], "MH_PICK_FILTER": FILTERS[file_filter]}
    try:
        result = run(["powershell.exe", "-NoProfile", "-NonInteractive", "-STA", "-EncodedCommand", encoded],
                     capture_output=True, text=True, encoding="utf-8", errors="replace",
                     timeout=DIALOG_TIMEOUT_S, env=environment)
    except subprocess.TimeoutExpired as exc:
        raise HubFailure(504, "PATH_PICKER_TIMEOUT", "The dialog was left open too long and was closed.") from exc
    except OSError as exc:
        raise HubFailure(501, "PATH_PICKER_UNAVAILABLE", f"The dialog could not be opened: {exc}. Type the path instead.") from exc
    if result.returncode != 0:
        raise HubFailure(502, "PATH_PICKER_FAILED", "The dialog could not be shown. Type the path instead.")
    path = (result.stdout or "").strip()
    return path or None
