"""Tests that span owners.

Each test here exercises owners that may not import one another - MonkeyArch's
runner with the tools' stage commands and MonkeyMonitor, CAD execution with a
drawing and the Hub web loader, the kernel's version-reference and format tables
with the CAD and drawing records they cover - or needs fixtures that span owners:
MonkeyCAD's backends accepted through MonkeyArch's runner, and the runner-driven
edits. support.py, runner_support.py and window_support.py hold what they share.
A test leaves for a package's own tests only where that package's fixtures serve
it, with no copy over about 100 lines.
"""
