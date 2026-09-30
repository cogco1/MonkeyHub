"""The Hub's settings: the local account's preferences and this Hub's launch choices.

``models`` holds their two DTOs, ``store`` reads and atomically replaces their
files, and ``routes`` serves ``GET/PUT /api/settings/user``. The Project Runtime
reads neither file; it receives the resolved values in its launch environment.
``credentials`` keeps the provider keys entered in settings in the account's
Windows Credential Manager, never in either file.
"""
