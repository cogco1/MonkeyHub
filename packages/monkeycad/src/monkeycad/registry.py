"""The one table every compiled-CAD backend is registered in; the runner selects and executes through it."""

from __future__ import annotations

from monkeycad.backends.blender.backend import BlenderBackend
from monkeycad.backends.occt.backend import OcctBackend
from monkeycad.backends.rhino.backend import RhinoBackend
from monkeycad.execution import CadBackend, CadExecutionError


# Add an implementation here and declare it under compiled-cad-execution in the
# module registry. The runner uses this same table for selection and execution.
CAD_BACKEND_REGISTRY: dict[str, CadBackend] = {backend.backend_id: backend for backend in (OcctBackend(), RhinoBackend(), BlenderBackend())}


def get_cad_backend(backend_id: str) -> CadBackend:
    try:
        return CAD_BACKEND_REGISTRY[backend_id]
    except KeyError as exc:
        raise CadExecutionError(f"unknown cad_backend {backend_id!r}; registered: {tuple(CAD_BACKEND_REGISTRY)}") from exc


def cad_backend_ids() -> tuple[str, ...]:
    return tuple(CAD_BACKEND_REGISTRY)
