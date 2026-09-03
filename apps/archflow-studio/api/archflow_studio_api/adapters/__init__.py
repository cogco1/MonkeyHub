"""Adapters: authored project input read onto the kernel's own types.

Nothing here decides anything. Each module takes a file the project already
holds and hands back the kernel object it names, so the application layer can
call ``run_project`` with the seats and the guard the project itself declares
rather than with something the API invented.
"""
