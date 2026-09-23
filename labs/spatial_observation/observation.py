"""Selective observation of existing geometry; no project state or LLM judgment.

Bounds select what to look at. The existing OCCT adapter measures the solids.
All coordinates exposed by this module are metres in the input CAD frame.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Mapping, Sequence

from archflow.adapters.occt_backend import StepEntry, measure_occt_solid_pairs, measure_shape


@dataclass(frozen=True)
class Binding:
    project_id: str
    run_id: str
    record_digest: str


@dataclass(frozen=True)
class Annotation:
    """Caller hypotheses remain separate from measured shape properties.

    geometry_identity, if supplied, is the existing compiler object's identity.
    This module does not invent a third identity for an observation.
    """
    description: str = ""
    role: str | None = None
    dependencies: tuple[str, ...] = ()
    geometry_identity: str | None = None


@dataclass(frozen=True)
class ObservedObject:
    object_id: str
    minimum: tuple[float, float, float]
    maximum: tuple[float, float, float]
    volume_m3: float | None
    valid: bool
    closed: bool
    annotation: Annotation


@dataclass(frozen=True)
class Selection:
    binding: Binding
    objects: tuple[ObservedObject, ...]
    seed_ids: tuple[str, ...]
    omitted_ids: tuple[str, ...]
    unresolved_dependencies: tuple[str, ...]
    reason: str


def bounds_gap(a: ObservedObject, b: ObservedObject) -> float:
    """Lower bound on solid distance; not proof of contact or support."""
    return math.sqrt(sum(max(0.0, a.minimum[i] - b.maximum[i],
                             b.minimum[i] - a.maximum[i]) ** 2 for i in range(3)))


def _tokens(value: str) -> set[str]:
    return set(re.findall(r"\w+", value.casefold()))


class Scene:
    def __init__(self, entries: Sequence[StepEntry], *, binding: Binding,
                 length_unit: str, annotations: Mapping[str, Annotation] | None = None):
        if not all((binding.project_id, binding.run_id, binding.record_digest)):
            raise ValueError("a scene needs the exact project, run and record binding")
        scale = {"meter": 1.0, "millimeter": .001, "inch": .0254, "foot": .3048}.get(length_unit)
        if scale is None:
            raise ValueError(f"unsupported length unit: {length_unit}")
        self.binding = binding
        self.length_unit = length_unit
        self._to_m = scale
        self._entries = tuple(entries)
        self.objects: dict[str, ObservedObject] = {}
        annotations = annotations or {}
        for entry in entries:
            if not entry.name or entry.name in self.objects:
                raise ValueError("every delivered shape needs a unique object id")
            m = measure_shape(entry.shape)
            self.objects[entry.name] = ObservedObject(
                entry.name, tuple(v * scale for v in m.bbox_min),
                tuple(v * scale for v in m.bbox_max),
                None if m.volume is None else m.volume * scale ** 3,
                m.valid, m.closed, annotations.get(entry.name, Annotation()))
        unknown = set(annotations) - self.objects.keys()
        if unknown:
            raise ValueError(f"annotations name absent objects: {sorted(unknown)}")

    def require(self, binding: Binding) -> None:
        if binding != self.binding:
            raise ValueError("stale or foreign observation: query the intended scene revision")

    def select(self, *, binding: Binding, ids: Sequence[str] = (), text: str = "",
               radius_m: float | None = None, hops: int = 1,
               max_objects: int | None = None) -> Selection:
        """Lexical seeds, optional spatial expansion, then dependency closure.

        A radius is explicit and metric, not a universal architectural threshold.
        max_objects bounds the response, never conceals what was omitted.
        Dependency closure also follows reverse references to expose affected work.
        """
        self.require(binding)
        if radius_m is not None and (not math.isfinite(radius_m) or radius_m < 0):
            raise ValueError("radius must be finite and non-negative")
        if hops < 0 or max_objects is not None and max_objects < 1:
            raise ValueError("hops must be non-negative and max_objects positive")
        if set(ids) - self.objects.keys():
            raise ValueError(f"unknown seed ids: {sorted(set(ids) - self.objects.keys())}")
        terms = _tokens(text)
        seeds = set(ids)
        if terms:
            for obj in self.objects.values():
                words = _tokens(f"{obj.object_id} {obj.annotation.description} {obj.annotation.role or ''}")
                if terms <= words:
                    seeds.add(obj.object_id)
        selected = set(seeds)
        frontier = set(seeds)
        if radius_m is not None:
            for _ in range(hops):
                nearby = {other.object_id for key in frontier for other in self.objects.values()
                          if bounds_gap(self.objects[key], other) <= radius_m}
                frontier = nearby - selected
                selected |= nearby
        missing: set[str] = set()
        pending = list(selected)
        while pending:
            key = pending.pop()
            related = set(self.objects[key].annotation.dependencies)
            related |= {o.object_id for o in self.objects.values() if key in o.annotation.dependencies}
            missing |= related - self.objects.keys()
            new = related & self.objects.keys() - selected
            selected |= new
            pending.extend(new)
        # Seeds stay first; neighborhood order is stable across dictionary layouts.
        ordered = sorted(selected, key=lambda key: (key not in seeds, key))
        kept = ordered if max_objects is None else ordered[:max_objects]
        omitted = ordered[len(kept):]
        return Selection(self.binding, tuple(self.objects[k] for k in kept), tuple(sorted(seeds)),
                         tuple(omitted), tuple(sorted(missing)),
                         "lexical/explicit seeds; bounds proximity is tentative; dependency closure")

    def exact_pairs(self, *, binding: Binding, pairs: Sequence[tuple[str, str]]) -> dict:
        self.require(binding)
        return measure_occt_solid_pairs(self._entries, object_pairs=pairs, length_unit=self.length_unit)

    def segment_hits(self, *, binding: Binding, start_m: Sequence[float],
                     end_m: Sequence[float], ignore_ids: Sequence[str] = (),
                     tolerance_m: float = 1e-7) -> dict:
        """Intersect a finite 3D sight line with actual faces, preserving holes.

        This is geometric occlusion, not a lighting simulation. The caller names
        glass or other surfaces it elects to look through; unknown roles remain
        opaque by default. A partial kernel failure cannot establish visibility.
        """
        self.require(binding)
        from OCP.BRepClass3d import BRepClass3d_SolidClassifier
        from OCP.IntCurvesFace import IntCurvesFace_ShapeIntersector
        from OCP.TopAbs import TopAbs_IN, TopAbs_ON, TopAbs_SOLID
        from OCP.TopExp import TopExp_Explorer
        from OCP.gp import gp_Dir, gp_Lin, gp_Pnt
        if len(start_m) != 3 or len(end_m) != 3 or not all(
                math.isfinite(v) for v in (*start_m, *end_m)):
            raise ValueError("sight line endpoints must be finite 3D points")
        if not math.isfinite(tolerance_m) or tolerance_m <= 0:
            raise ValueError("tolerance must be finite and positive")
        if set(ignore_ids) - self.objects.keys():
            raise ValueError("ignored surfaces must exist in this revision")
        vector = tuple(end_m[i] - start_m[i] for i in range(3))
        distance = math.sqrt(sum(v * v for v in vector))
        if distance <= tolerance_m:
            raise ValueError("sight line is shorter than tolerance")
        line = gp_Lin(gp_Pnt(*(v / self._to_m for v in start_m)), gp_Dir(*vector))
        hits, unavailable, occupied_endpoints = [], [], []
        for entry in self._entries:
            if entry.name in ignore_ids:
                continue
            intersector = IntCurvesFace_ShapeIntersector()
            try:
                solids = TopExp_Explorer(entry.shape, TopAbs_SOLID)
                while solids.More():
                    for label, point in (("start", start_m), ("end", end_m)):
                        classifier = BRepClass3d_SolidClassifier(
                            solids.Current(), gp_Pnt(*(v / self._to_m for v in point)),
                            tolerance_m / self._to_m)
                        if classifier.State() in (TopAbs_IN, TopAbs_ON):
                            occupied_endpoints.append({"object_id": entry.name, "endpoint": label,
                                "state": "inside" if classifier.State() == TopAbs_IN else "boundary"})
                    solids.Next()
                intersector.Load(entry.shape, tolerance_m / self._to_m)
                intersector.Perform(line, 0.0, distance / self._to_m)
                if not intersector.IsDone():
                    raise ValueError("surface intersection did not complete")
                for index in range(1, intersector.NbPnt() + 1):
                    p = intersector.Pnt(index)
                    hits.append({"object_id": entry.name,
                                 "distance_m": intersector.WParameter(index) * self._to_m,
                                 "point_m": (p.X() * self._to_m, p.Y() * self._to_m, p.Z() * self._to_m)})
            except Exception as exc:
                unavailable.append({"object_id": entry.name, "detail": str(exc)})
        hits.sort(key=lambda hit: (hit["distance_m"], hit["object_id"]))
        return {"hits": hits, "unavailable": unavailable, "occupied_endpoints": occupied_endpoints,
                "tolerance_m": tolerance_m,
                "ignored_ids": tuple(ignore_ids)}


def changes(previous: Scene, current: Scene) -> dict[str, tuple[str, ...]]:
    """Describe changed observations without assuming equal bounds mean equal shapes."""
    if previous.binding.project_id != current.binding.project_id:
        raise ValueError("cannot compare different projects")
    old, new = previous.objects, current.objects
    shared = old.keys() & new.keys()
    moved, reshaped, semantic, unresolved = [], [], [], []
    for key in sorted(shared):
        a, b = old[key], new[key]
        if (a.minimum, a.maximum) != (b.minimum, b.maximum):
            moved.append(key)
        ai, bi = a.annotation.geometry_identity, b.annotation.geometry_identity
        if ai is None or bi is None:
            unresolved.append(key)
        elif ai != bi:
            reshaped.append(key)
        if (a.annotation.role, a.annotation.description, a.annotation.dependencies) != (
                b.annotation.role, b.annotation.description, b.annotation.dependencies):
            semantic.append(key)
    return {"added": tuple(sorted(new.keys() - old.keys())),
            "removed": tuple(sorted(old.keys() - new.keys())),
            "bounds_changed": tuple(moved), "geometry_changed": tuple(reshaped),
            "annotation_changed": tuple(semantic), "geometry_change_unknown": tuple(unresolved)}
