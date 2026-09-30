"""Tests that span owners.

Each test here exercises owners that may not import one another - MonkeyArch's
runner with the tools' stage commands and MonkeyMonitor, CAD execution with a
drawing and the Hub web loader, the kernel's version-reference and format tables
with the CAD and drawing records they cover - or reuses such a test's fixture.
support.py holds the copies of the owners' fixtures they share; a test of one
package, with what that package may import, lives in that package's tests.
"""
