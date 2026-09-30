"""The authoring contracts the element producers advertise.

``producer_signatures`` is the table of producers a person or an agent may
author directly: each one's parameters, references and constraints, in the small
JSON-schema vocabulary that ``element_producers.validate_element_contract``
checks an edited row against before a candidate is queued. ``parameter_unit`` is
the unit of each advertised numeric parameter. Values and placements belong to
the project's record, never to these tables.

The producers, their edits and that check stay in ``element_producers``. The
runner's ``_producer_code`` digests this module with them, so changing a table
here invalidates reuse exactly as it did while the tables lived there.
"""
from __future__ import annotations

from typing import Any


def producer_signatures() -> dict[str, dict[str, Any]]:
    """The authoring contracts the Studio can query and execute.

    Prisms, bounded planar surfaces, curves, lofts and walls with hosted apertures expose their
    authored parameters here. Other existing producers remain executable;
    they are not advertised as semantic creation tools until their authored
    parameter contract is exposed here.
    Values and placements belong to the project's record, never this table.
    """

    def obj(properties: dict[str, Any], required: tuple[str, ...] = ()) -> dict[str, Any]:
        return {"type": "object", "properties": properties,
                "required": list(required), "additionalProperties": False}

    scalar = {"anyOf": [{"type": "number"}, {"type": "string", "pattern": r"^@[A-Za-z0-9_.:-]+$"}],
              "description": "A value in metres, or an explicit @parameter binding."}
    identifier = {"type": "string", "minLength": 1}
    grid_role = {**identifier, "description": "Optional reference system: the role (not entity_id) of a GridAxis@1 the project already declares."}
    level_id = {**identifier, "description": "The existing Level@1 entity_id, not its role."}
    plan = {"anyOf": [
        obj({"point": {"type": "array", "items": scalar, "minItems": 2, "maxItems": 2,
                       "description": "Explicit project-local [x, z] in metres; coordinates may bind @parameters. No grid is required."}}, ("point",)),
        obj({"grid": {"anyOf": [grid_role, {"type": "array", "items": grid_role,
                                                "minItems": 2, "maxItems": 2}]}}, ("grid",)),
        obj({"axis_point": obj({"axis": grid_role, "along": scalar}, ("axis", "along"))}, ("axis_point",)),
        obj({"host": obj({"element": identifier, "along": scalar, "across": scalar},
                         ("element", "along"))}, ("host",)),
    ]}
    elevation = {"anyOf": [
        obj({"level": level_id}, ("level",)),
        obj({"datum": identifier, "offset": scalar}, ("datum",)),
        obj({"offset_from": obj({"level": level_id, "offset": scalar}, ("level", "offset"))},
            ("offset_from",)),
    ]}
    # Hosted opening elevations use the level resolver; published datums are
    # supported by the wall's base/top resolver only.
    opening_elevation = {"anyOf": [elevation["anyOf"][0], elevation["anyOf"][2]]}
    voids = {"type": "array", "items": identifier, "minItems": 1, "description": (
        "Elements whose solids are removed from this one. Each keeps its own identity and stays in the model "
        "hidden; remove it from this list and it shows again. Neither element needs to be classified. "
        "A void is a prism without rectangular_cutouts or a capped loft, has no voids of its own, and nothing "
        "stands on its top.")}
    opening = obj({
        "opening_id": identifier,
        "component_id": identifier,
        "kind": {"type": "string", "enum": ["door", "window"]},
        "shape": {"type": "string", "enum": ["rectangular", "semicircular_arch"],
                  "description": "The aperture profile. An unfilled doorway has no door leaf."},
        "along": scalar,
        "at": plan,
        "width": scalar,
        "sill": {"anyOf": [scalar, opening_elevation]},
        "head": {"anyOf": [scalar, opening_elevation]},
        "spring_height": {**scalar, "description":
            "For semicircular_arch only: springing above the wall base; head - spring_height = width / 2."},
        "count": {"anyOf": [{"type": "integer", "minimum": 1},
                              {"type": "string", "pattern": r"^@[A-Za-z0-9_.:-]+$"}]},
        "step": scalar,
        "type_id": {**identifier, "description": "Only an existing compatible rectangular window or door type; omit for an empty passage."},
        "interface_ref": identifier,
    }, ("opening_id", "kind", "width", "sill", "head"))
    # A drawn outline pulled to a height: the same row this module has always
    # produced, now stated as an authoring contract so a person drawing on the
    # model reaches it the way an agent reaches the wall.
    plan_point = {"type": "array", "items": scalar, "minItems": 2, "maxItems": 2}
    vector3 = {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3}
    work_plane = obj({"origin": vector3, "xAxis": vector3, "yAxis": vector3, "normal": vector3},
                     ("origin", "xAxis", "yAxis", "normal"))
    cutout = obj({
        "cutout_id": identifier,
        "span0": {"type": "number"}, "span1": {"type": "number"},
        "bottom": {"type": "number"}, "top": {"type": "number"},
    }, ("cutout_id", "span0", "span1", "bottom", "top"))
    prism = {
        "producer": "prism",
        "label": "轮廓与高度",
        "description": (
            "A closed profile extruded to a height, anchored to an absolute elevation, an existing level or another "
            "element's published top. An optional work_plane places it on an explicit drawing face. "
            "The profile and the height stay the record's own parameters, so "
            "either can be changed afterwards by authoring the same element again. Rectangular cutouts "
            "trim an axis-aligned rectangular profile through its thickness; a profile that is not such "
            "a rectangle carries no cutouts."
        ),
        "parameters": obj({
            "profile": {"type": "array", "items": plan_point, "minItems": 3,
                        "description": "The plan profile as a closed boundary in order; repeating its first point at the end is optional. Coordinates may bind @parameters."},
            "height": {**scalar, "description": "How far the profile is pulled; optional when references.top determines it."},
            "elevation": {**scalar, "description":
                "Metres above references.base, added to that reference's own offset; defaults to zero. "
                "It moves the whole prism and leaves the height alone."},
            "work_plane": {**work_plane, "description":
                "Optional orthonormal drawing frame relative to references.base. Profile pairs follow "
                "xAxis/yAxis; height follows normal. Omit for the retained XZ/+Y convention."},
            "rectangular_cutouts": {"type": "array", "items": cutout,
                                    "description": "Openings through an axis-aligned rectangular profile."},
        }),
        "references": obj({
            "base": {"anyOf": [*elevation["anyOf"], obj({"elevation": scalar}, ("elevation",))]},
            "top": elevation,
            "voids": voids,
        }),
        "requiredParameters": ["profile"],
        "requiredReferences": ["base"],
        "constraints": [
            "Provide either height or references.top; a prism with neither has no height to build.",
            "With a top reference, base datum plus the reference offset plus elevation plus height must agree with it.",
            "Without a top reference, changing elevation moves the whole prism and changing height keeps its bottom fixed.",
            "A profile needs at least three distinct points; repeating the first point at the end is optional and changes nothing.",
            "rectangular_cutouts require four ordered axis-aligned profile corners.",
            "Use @parameter bindings for dimensions that subsequent changes must share.",
            "The element this one stands on is named by references.base, never inferred from proximity.",
            "An absolute references.base.elevation stays fixed when project levels or other elements change.",
            "work_plane axes are orthonormal; origin is relative to the base datum, and height follows normal.",
            "Only a horizontal upward extrusion publishes a horizontal top datum; tilted planes cannot claim one.",
            "An opening, recess or cut is another prism or capped loft named in references.voids; rectangular_cutouts stay for panels that already use them.",
        ],
    }
    signatures = {"wall": {
        "producer": "wall",
        "label": "墙体与宿主开口",
        "description": (
            "A straight wall placed by explicit project-local points; existing grids or hosts are optional reference systems. Its hosted opening may be "
            "rectangular or semicircular. No opening type means an empty passage, with no frame or leaf. "
            "Types supply reusable defaults; instance params and references override named defaults. "
            "References and dimensions must come from the project or an explicit design proposal. "
            "A supporting wall ends at the supported slab's underside, not its walking surface."
        ),
        "parameters": obj({
            "height": {**scalar, "description": "Wall height; optional when references.top determines it."},
            "thickness": {**scalar, "description":
                "Positive wall thickness. The line is one face of the wall; seen from above, the thickness lies "
                "to the left of the line walked from its from point to its to point, unless references.line.inward says otherwise."},
            "openings": {"type": "array", "items": opening},
        }),
        "references": obj({
            "base": {"anyOf": [obj({"level": level_id}, ("level",)), obj({"datum": identifier}, ("datum",))]},
            "top": elevation,
            "support": {**identifier, "description": "Existing support reference; bearing is declared by a named support relationship."},
            "line": obj({"from": plan, "to": plan,
                         "face": {"type": "string", "description": "The existing wall face label, when the record names one."},
                         "inward": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2,
                                    "description": "Optional plan [X, Z] direction towards the side the thickness goes; "
                                                   "pointing to the right of from→to puts the thickness on the right. "
                                                   "The line stays the same face either way."}}, ("from", "to")),
            "voids": voids,
        }),
        "requiredParameters": ["thickness"],
        "requiredReferences": ["base", "line"],
        "constraints": [
            "Provide either height or references.top; if both are stated they must agree.",
            "Seen from above, thickness lies to the left of from→to; line.inward pointing to the right moves it to the right, and the line stays the wall's face.",
            "A parapet or facade wall whose line is the facade: point line.inward into the building, or order from→to so the building is on the left, to keep the thickness inside the facade line.",
            "Each aperture needs along or at, and remains inside its wall's length and height.",
            "A semicircular aperture requires spring_height >= sill and head - spring_height = width / 2.",
            "Use @parameter bindings for dimensions that subsequent changes must share.",
            "Use existing relation kinds for support, host, adjacency or clearance; proximity does not prove support.",
            "references.voids removes other elements' solids through the same cut as the openings; the wall stays obj-<wall>.",
        ],
    }, "prism": prism, "loft": {
        "producer": "loft",
        "label": "多截面形体",
        "description": (
            "One shape through ordered closed polygon sections, for tapered or varying-section forms. "
            "Supply all sections together instead of separate stacked prisms. Coordinates stay bound "
            "to the record's parameters for subsequent edits."
        ),
        "parameters": obj({
            "profiles": {"type": "array", "minItems": 2, "items": {
                "type": "array", "minItems": 3, "items": {
                    "type": "array", "items": scalar, "minItems": 3, "maxItems": 3,
                }}, "description": "Ordered sections of [X, Y-up, Z] points relative to the base datum; coordinates may bind @parameters."},
            "profile_size": {"type": "integer", "minimum": 3,
                             "description": "The number of distinct vertices in every section, not counting a repeated first point."},
            "loft_type": {"type": "string", "enum": ["straight", "normal"],
                          "description": "straight (default) connects sections with ruled faces; normal interpolates between the sections."},
            "profile_basis": {"type": "string", "enum": ["polyline"],
                              "description": "Polyline sections (default); interpolated section curves are not exposed by this authoring path."},
            "cap_ends": {"type": "boolean", "description": "True (default) caps both ends into one solid; false leaves both end sections open as a surface."},
            "closed_profile": {"type": "boolean", "enum": [True]},
        }),
        "references": obj({"base": {"anyOf": [obj({"level": level_id}, ("level",)),
                                               obj({"datum": identifier}, ("datum",))]}, "voids": voids}),
        "requiredParameters": ["profiles", "profile_size"],
        "requiredReferences": ["base"],
        "constraints": [
            "Provide at least two simple closed polygon sections with the same vertex count and corresponding vertex order.",
            "A section may repeat its first point at the end (profile_size + 1 points) or not (profile_size points); the loft closes each section without joining the first and last sections.",
            "A section of exactly profile_size points whose last point repeats its first is refused: it is ambiguous whether profile_size counted the repeat.",
            "Section coordinates carry their height relative to the base datum; do not add a separate base offset, height or elevation.",
            "One loft creates one object; an uncapped loft is a surface with no invented wall thickness.",
            "Use @parameter coordinates and parameter expressions for dimensions that subsequent edits must share.",
            "A loft publishes no horizontal top datum; do not reference <element-id>-top from another element.",
            "Only a capped loft (cap_ends true) can host voids or act as one.",
        ],
    }, "planar-surface": {
        "producer": "planar-surface",
        "label": "可见面",
        "description": (
            "A visible planar surface with a stated boundary and elevation: what a floor or a "
            "ceiling shows in a drawing, without a construction thickness and without inventing the "
            "solid that would hold it up. An optional work_plane places a drawn face in any stated orientation."
        ),
        "parameters": obj({
            "profile": {"type": "array", "items": plan_point, "minItems": 3,
                        "description": "One simple closed boundary in work_plane coordinates (XZ when omitted), in order; repeating its first point at the end is optional."},
            "elevation": {**scalar, "description":
                "Metres above references.base, added to that reference's own offset; defaults to zero."},
            "work_plane": work_plane,
        }),
        "references": obj({"base": {"anyOf": [*elevation["anyOf"], obj({"datum": identifier}, ("datum",)),
                                               obj({"elevation": scalar}, ("elevation",))]}}),
        "requiredParameters": ["profile"],
        "requiredReferences": ["base"],
        "constraints": [
            "The profile is one simple boundary of at least three distinct points; repeating the first point at the end is optional and changes nothing; holes are unsupported.",
            "The surface elevation is its resolved base datum plus the reference offset plus parameters.elevation.",
            "Use an explicit @parameter binding for an elevation that subsequent changes must share.",
        ],
    }, "curve": {
        "producer": "curve",
        "label": "线与曲线",
        "description": "One drawn polyline, including sampled freehand paths and arcs, without a face or thickness.",
        "parameters": obj({
            "profile": {"type": "array", "items": plan_point, "minItems": 2, "maxItems": 512,
                        "description": "Ordered points in work_plane coordinates (XZ when omitted); no closing segment is added."},
            "elevation": scalar,
            "work_plane": work_plane,
        }),
        "references": obj({"base": elevation}),
        "requiredParameters": ["profile"],
        "requiredReferences": ["base"],
        "constraints": [
            "At least two points are required; consecutive points must be distinct.",
            "The curve follows its base datum, reference offset, elevation and explicit work_plane.",
            "A curve creates no face, thickness, support relation or top datum.",
        ],
    }}
    # Agents read this table in order, so it runs from the lowest sufficient
    # expression to the specialized realization: early modeling starts with a
    # profile, a face or a path, and a wall is chosen when its meaning is
    # established (#400). No caller may depend on this order. A producer not
    # yet ranked here follows the ranked ones instead of being dropped.
    ranked = ("prism", "planar-surface", "curve", "loft", "wall")
    return {name: signatures[name] for name in (*ranked, *sorted(set(signatures) - set(ranked)))}


# The unit each advertised numeric parameter is authored in (#404 F17): every
# length the producers read is metres (``element_producers._M``, and the
# signatures' "a value in metres"); counts, flags and choices carry none.
_PARAMETER_UNITS: dict[str, dict[str, str]] = {
    "prism": {"height": "m", "elevation": "m"},
    "planar-surface": {"elevation": "m"},
    "curve": {"elevation": "m"},
    "wall": {"height": "m", "thickness": "m"},
}


def parameter_unit(producer: str, key: str) -> str | None:
    """The unit an advertised producer declares for one numeric parameter, or None when it declares none."""

    return _PARAMETER_UNITS.get(producer, {}).get(key)
