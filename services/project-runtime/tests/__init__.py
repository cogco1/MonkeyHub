"""The Project Runtime's tests.

The runtime package lives in ``src`` beside this directory, which goes first on
``sys.path`` when it is missing. Importing the package then puts this checkout's
other Python source roots there too (project_runtime/__init__.py), so a test run
from the service directory imports this checkout's packages, whatever else is
installed.
"""

from pathlib import Path
import sys

_SOURCE = str(Path(__file__).resolve().parents[1] / "src")
if _SOURCE not in sys.path:
    sys.path.insert(0, _SOURCE)

import project_runtime  # noqa: E402,F401
