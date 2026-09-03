"""Deterministic SVG paper views over an immutable hybrid sandbox scene."""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping

from archflow.realization import HybridScene, SceneObject, SceneRepresentation
from archflow.contracts.canonical import canonical_digest, canonical_json


class SandboxRenderError(ValueError):
    """A scene cannot be represented under the explicit render policy."""


class SandboxViewKind(StrEnum):
    ISO = "iso"
    PLAN = "plan"
    LONGITUDINAL_SECTION = "longitudinal_section"
    TRANSVERSE_SECTION = "transverse_section"
    ELEVATION = "elevation"


def _sha(value: object, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value.lower())
    ):
        raise SandboxRenderError(f"{field} must be a SHA-256 digest")
    return value.lower()


@dataclass(frozen=True, slots=True)
class SandboxRenderPolicy:
    width: int = 1200
    height: int = 800
    margin: int = 48
    stroke_width: float = 1.25

    SCHEMA = "SandboxRenderPolicy@1"

    def __post_init__(self) -> None:
        for field in ("width", "height", "margin"):
            value = getattr(self, field)
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or value <= 0
            ):
                raise SandboxRenderError(f"{field} must be positive")
        if self.margin * 2 >= min(self.width, self.height):
            raise SandboxRenderError("render margin consumes the page")
        if (
            not isinstance(self.stroke_width, (int, float))
            or isinstance(self.stroke_width, bool)
            or not math.isfinite(float(self.stroke_width))
            or self.stroke_width <= 0
        ):
            raise SandboxRenderError("stroke_width must be positive")
        object.__setattr__(self, "stroke_width", float(self.stroke_width))

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "width": self.width,
            "height": self.height,
            "margin": self.margin,
            "stroke_width": self.stroke_width,
        }


@dataclass(frozen=True, slots=True)
class SandboxPaperView:
    kind: SandboxViewKind
    scene_digest: str
    policy_digest: str
    svg_text: str

    SCHEMA = "SandboxPaperView@1"

    def __post_init__(self) -> None:
        if not isinstance(self.kind, SandboxViewKind):
            raise TypeError("kind must be SandboxViewKind")
        object.__setattr__(
            self,
            "scene_digest",
            _sha(self.scene_digest, "scene_digest"),
        )
        object.__setattr__(
            self,
            "policy_digest",
            _sha(self.policy_digest, "policy_digest"),
        )
        if (
            not isinstance(self.svg_text, str)
            or not self.svg_text.startswith("<svg ")
            or not self.svg_text.endswith("</svg>")
        ):
            raise SandboxRenderError("svg_text must be one complete SVG")
        if f'data-scene-digest="{self.scene_digest}"' not in self.svg_text:
            raise SandboxRenderError("SVG lost exact scene binding")

    @property
    def svg_sha256(self) -> str:
        return hashlib.sha256(self.svg_text.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "kind": self.kind.value,
            "scene_digest": self.scene_digest,
            "policy_digest": self.policy_digest,
            "svg_text": self.svg_text,
            "svg_sha256": self.svg_sha256,
            "repair_authority": False,
            "canonical_write_authority": False,
        }

    @classmethod
    def from_dict(cls, value: object) -> SandboxPaperView:
        if not isinstance(value, Mapping) or set(value) != {
            "schema",
            "kind",
            "scene_digest",
            "policy_digest",
            "svg_text",
            "svg_sha256",
            "repair_authority",
            "canonical_write_authority",
        }:
            raise SandboxRenderError("paper view schema drifted")
        if (
            value["schema"] != cls.SCHEMA
        ):
            raise SandboxRenderError("paper view authority drifted")
        result = cls(
            kind=SandboxViewKind(value["kind"]),
            scene_digest=value["scene_digest"],
            policy_digest=value["policy_digest"],
            svg_text=value["svg_text"],
        )
        if value["svg_sha256"] != result.svg_sha256:
            raise SandboxRenderError("paper view SVG digest drifted")
        return result


@dataclass(frozen=True, slots=True)
class SandboxRenderSet:
    scene_digest: str
    policy_digest: str
    views: tuple[SandboxPaperView, ...]

    SCHEMA = "SandboxRenderSet@1"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "scene_digest",
            _sha(self.scene_digest, "scene_digest"),
        )
        object.__setattr__(
            self,
            "policy_digest",
            _sha(self.policy_digest, "policy_digest"),
        )
        if not isinstance(self.views, tuple) or any(
            not isinstance(item, SandboxPaperView) for item in self.views
        ):
            raise TypeError("views contains an invalid item")
        kinds = tuple(item.kind for item in self.views)
        expected = tuple(SandboxViewKind)
        if kinds != expected:
            raise SandboxRenderError(
                "render set requires all views in deterministic order"
            )
        if any(
            item.scene_digest != self.scene_digest
            or item.policy_digest != self.policy_digest
            for item in self.views
        ):
            raise SandboxRenderError("render views disagree with their set")

    @property
    def render_digest(self) -> str:
        return canonical_digest(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "scene_digest": self.scene_digest,
            "policy_digest": self.policy_digest,
            "views": [item.to_dict() for item in self.views],
            "repair_authority": False,
            "canonical_write_authority": False,
        }

    @classmethod
    def from_dict(cls, value: object) -> SandboxRenderSet:
        if not isinstance(value, Mapping) or set(value) != {
            "schema",
            "scene_digest",
            "policy_digest",
            "views",
            "repair_authority",
            "canonical_write_authority",
        }:
            raise SandboxRenderError("render set schema drifted")
        if (
            value["schema"] != cls.SCHEMA
        ):
            raise SandboxRenderError("render set authority drifted")
        return cls(
            scene_digest=value["scene_digest"],
            policy_digest=value["policy_digest"],
            views=tuple(
                SandboxPaperView.from_dict(item) for item in value["views"]
            ),
        )


Point3 = tuple[float, float, float]
Segment3 = tuple[Point3, Point3, str]


def _box_segments(
    minimum: Point3,
    maximum: Point3,
    style: str,
) -> tuple[Segment3, ...]:
    corners = {
        (x, y, z): (x, y, z)
        for x in (minimum[0], maximum[0])
        for y in (minimum[1], maximum[1])
        for z in (minimum[2], maximum[2])
    }
    segments = []
    for point in sorted(corners):
        for axis in range(3):
            other = list(point)
            other[axis] = maximum[axis]
            other_point = tuple(other)
            if other_point != point and other_point in corners:
                segments.append((point, other_point, style))
    return tuple(segments)


def _apply_matrix(
    matrix: tuple[float, ...],
    point: Point3,
) -> Point3:
    value = (*point, 1.0)
    return tuple(
        sum(matrix[row * 4 + column] * value[column] for column in range(4))
        for row in range(3)
    )


def _object_segments(
    item: SceneObject,
    objects: Mapping[str, SceneObject],
    style: str,
) -> tuple[Segment3, ...]:
    geometry = item.geometry
    if geometry["kind"] == "polyline":
        points = tuple(tuple(point) for point in geometry["points"])
        return tuple(
            (first, second, style)
            for first, second in zip(points, points[1:])
        )
    if geometry["kind"] == "mesh":
        points = tuple(tuple(point) for point in geometry["vertices"])
        edges = {
            tuple(sorted((face[index], face[(index + 1) % len(face)])))
            for face in geometry["faces"]
            for index in range(len(face))
        }
        return tuple(
            (points[first], points[second], style)
            for first, second in sorted(edges)
        )
    if geometry["kind"] == "transform":
        matrix = tuple(geometry["matrix"])
        return tuple(
            (_apply_matrix(matrix, first), _apply_matrix(matrix, second), style)
            for first, second, _ in _object_segments(
                objects[geometry["input"]],
                objects,
                style,
            )
        )
    if geometry["kind"] == "array":
        source_segments = _object_segments(
            objects[geometry["input"]],
            objects,
            style,
        )
        return tuple(
            (
                tuple(first[index] + offset[index] for index in range(3)),
                tuple(second[index] + offset[index] for index in range(3)),
                style,
            )
            for offset in geometry["offsets"]
            for first, second, _ in source_segments
        )
    return _box_segments(item.bounds.minimum, item.bounds.maximum, style)


def _scene_segments(scene: HybridScene) -> tuple[Segment3, ...]:
    segments: list[Segment3] = []
    objects = {item.object_id: item for item in scene.objects}
    for item in scene.objects:
        style = (
            "mesh"
            if item.representation is SceneRepresentation.MESH
            else "physical"
            if item.physical
            else "reference"
        )
        segments.extend(_object_segments(item, objects, style))
    if not segments:
        raise SandboxRenderError("scene contains no renderable geometry")
    return tuple(segments)


def _project(point: Point3, kind: SandboxViewKind) -> tuple[float, float]:
    x, y, z = point
    if kind is SandboxViewKind.ISO:
        return (x - z, (x + z) * 0.5 - y)
    if kind is SandboxViewKind.PLAN:
        return (x, -z)
    if kind is SandboxViewKind.TRANSVERSE_SECTION:
        return (z, -y)
    return (x, -y)


def _svg(
    scene: HybridScene,
    kind: SandboxViewKind,
    policy: SandboxRenderPolicy,
    policy_digest: str,
) -> str:
    projected = tuple(
        (_project(first, kind), _project(second, kind), style)
        for first, second, style in _scene_segments(scene)
    )
    all_points = tuple(
        point
        for first, second, _ in projected
        for point in (first, second)
    )
    min_x = min(point[0] for point in all_points)
    max_x = max(point[0] for point in all_points)
    min_y = min(point[1] for point in all_points)
    max_y = max(point[1] for point in all_points)
    extent_x = max(max_x - min_x, 1e-9)
    extent_y = max(max_y - min_y, 1e-9)
    scale = min(
        (policy.width - 2 * policy.margin) / extent_x,
        (policy.height - 2 * policy.margin) / extent_y,
    )

    def page(point: tuple[float, float]) -> tuple[float, float]:
        return (
            policy.margin + (point[0] - min_x) * scale,
            policy.margin + (point[1] - min_y) * scale,
        )

    line_values = []
    style_order = {"physical": 0, "mesh": 1, "reference": 2}
    for first, second, style in sorted(
        projected,
        key=lambda item: (
            style_order[item[2]],
            item[0],
            item[1],
        ),
    ):
        x1, y1 = page(first)
        x2, y2 = page(second)
        dash = ' stroke-dasharray="5 4"' if style == "reference" else ""
        opacity = "0.45" if style == "reference" else "0.85" if style == "mesh" else "1"
        line_values.append(
            (
                f'<line x1="{x1:.3f}" y1="{y1:.3f}" '
                f'x2="{x2:.3f}" y2="{y2:.3f}" '
                f'stroke="#111" stroke-opacity="{opacity}"'
                f'{dash}/>'
            )
        )
    lines = "".join(line_values)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{policy.width}" height="{policy.height}" '
        f'viewBox="0 0 {policy.width} {policy.height}" '
        f'data-view="{kind.value}" '
        f'data-scene-digest="{scene.scene_digest}" '
        f'data-policy-digest="{policy_digest}">'
        f'<rect width="100%" height="100%" fill="#fff"/>'
        f'<g fill="none" stroke-width="{policy.stroke_width:.3f}" '
        f'stroke-linecap="round" stroke-linejoin="round">{lines}</g>'
        f'<text x="{policy.margin}" y="{policy.height - policy.margin / 2:.3f}" '
        f'font-family="monospace" font-size="14" fill="#111">'
        f'{kind.value}</text></svg>'
    )


def render_paper_views(
    scene: HybridScene,
    *,
    policy: SandboxRenderPolicy = SandboxRenderPolicy(),
) -> SandboxRenderSet:
    """Render five exact-scene SVG views without mutating or repairing scene."""

    if not isinstance(scene, HybridScene):
        raise TypeError("scene must be HybridScene")
    if not isinstance(policy, SandboxRenderPolicy):
        raise TypeError("policy must be SandboxRenderPolicy")
    before = scene.scene_digest
    policy_digest = canonical_digest(policy.to_dict())
    views = tuple(
        SandboxPaperView(
            kind=kind,
            scene_digest=before,
            policy_digest=policy_digest,
            svg_text=_svg(scene, kind, policy, policy_digest),
        )
        for kind in SandboxViewKind
    )
    if scene.scene_digest != before:
        raise SandboxRenderError("renderer mutated the source scene")
    return SandboxRenderSet(
        scene_digest=before,
        policy_digest=policy_digest,
        views=views,
    )
