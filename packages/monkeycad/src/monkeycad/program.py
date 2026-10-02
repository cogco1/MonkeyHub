"""What every CAD document of a compiled program must carry: its objects' names, layers and ``archflow:*`` user text.

``expected_object_semantics`` derives this from the program alone, and each
backend's saved document is read back against it: producer op, binding ids,
component id, commitment and evidence refs, the per-component layer path, and
the operation's own statements.

Some forms are not recoverable from the geometry they leave behind: a
bounding box holds the same hull for a wedge rising along its run, one
rising across it and one rising the other way, and a hollow drum and a
solid one share a box entirely. Those producers therefore state their own
defining numbers on the operation, in ``GeometryOperation.statements``,
and every object of such an operation carries each statement as user text
beside its identity: the key verbatim under ``archflow:``, the value
verbatim. Nothing here keeps a table of which producer states what or
formats anything — a statement is already the text it will be written as,
so no geometry is measured or recomputed to produce a string, and an
object whose operation states nothing carries nothing extra. What it does
own is the identity namespace: a statement may not take a key the export
already writes (``producer_op``, ``object_ref``, ``operation_ref``,
``bindings``, ``component``, ``material``, ``material_status``,
``commitments``, ``evidence``, ``inspection_witness``), and one that tries
fails ``CadTranslationError`` rather than overwriting an object's identity.

Materials come only from the caller's ``material_by_component`` - the
``material.name`` its components declare - and ``material_by_part``, what
each part of a component wears when one of them declares its own (#580) -
and are never derived from a name or a shape: an object's name says only
which part delivered it. An object of a declaring component or part
carries ``archflow:material``; one whose components and part declare none
carries ``archflow:material_status`` = ``undeclared`` and keeps its layer's
distinction colour. A declared material wears one colour wherever it is
used: the caller's ``material_colors`` entry for it, else an identity colour
derived from the material's name (``_material_color``), which claims no
appearance.

The producers on the spine state these today — keys and formats exactly:

===========================  ==========================================
``archflow:wedge_low``       metres above the row's base datum
``archflow:wedge_high``      metres above the base datum, above ``low``
``archflow:wedge_axis``      ``along`` | ``across``
``archflow:wedge_sense``     ``+x`` | ``-x`` | ``+z`` | ``-z`` — the
                             direction the top rises in, anchored to the
                             kernel plan axes rather than to the row's
                             reference order
``archflow:shell_thickness`` metres of wall
``archflow:shell_kind``      ``cylinder`` | ``dome``
===========================  ==========================================

Metres are canonical decimal text (shortest round-trip repr: ``0.5``,
``2.0`` — no locale, no thousands separator), enumerated values their bare
literal; the producer that states them writes them that way. An older
export that predates the strings is not repaired here — the re-index keeps
such a row AMBIGUOUS and names what is missing.
"""

from __future__ import annotations

import hashlib
import re
from typing import Iterable, Mapping

from archflow.state.geometry_program import delivered_object_ids, operation_parameters
from archflow.state.state_record import part_of_object


ROOT_LAYER = "archflow"
_ROOT_LAYER = ROOT_LAYER  # the historical private name, kept for existing readers

# The ``archflow:*`` user-text keys the export itself writes: an object's
# identity, its layer semantics and its inspection role. They are the one
# namespace an operation's statements may not enter — a statement is free
# text the run declared, and no declaration may overwrite what identifies
# the object it travels on.
_RESERVED_USER_TEXT: frozenset[str] = frozenset(
    {
        "bindings",
        "commitments",
        "component",
        "evidence",
        "inspection_witness",
        "material",
        "material_status",
        "object_ref",
        "operation_ref",
        "producer_op",
    }
)

# ``archflow:material_status`` on an object whose components declare no
# material: it wears no native material and keeps its layer's distinction
# colour, and this says so rather than leaving the reader to infer it.
UNDECLARED_MATERIAL = "undeclared"


class CadTranslationError(ValueError):
    """The program contains a construct the translator cannot express."""


_LAYER_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _\-.]{0,63}$")


def _component_layer(
    components: tuple[str, ...],
    layer_by_component: Mapping[str, str] | None,
) -> str:
    """The layer an object's components put it on.

    Without a scheme: the historical ``archflow::<components>`` path. With a
    caller-supplied scheme (P108 numbered categories, e.g. ``20_STRUCTURE``):
    ``<category>::<components>`` — the category is the parent layer, the
    component keeps its own child layer, so identity survives the renumbering.
    The kernel never invents a category: an unmapped component stays on the
    historical path, visibly, rather than being guessed into a bucket.
    """

    if not components:
        return _ROOT_LAYER
    joined = "+".join(components)
    if layer_by_component is not None:
        categories = sorted(
            {
                layer_by_component[component]
                for component in components
                if component in layer_by_component
            }
        )
        if len(categories) == 1:
            category = categories[0]
            if not _LAYER_SEGMENT.match(category):
                raise ValueError(
                    f"layer category {category!r} is not a valid layer name"
                )
            return f"{category}::{joined}"
    return f"{_ROOT_LAYER}::{joined}"


def _layer_color(component_key: str) -> tuple[int, int, int]:
    digest = hashlib.sha256(component_key.encode("utf-8")).digest()
    return (
        60 + digest[0] % 160,
        60 + digest[1] % 160,
        60 + digest[2] % 160,
    )


def _material_identity_color(material: str) -> tuple[int, int, int]:
    """The colour that tells one declared material from another until its own colour is declared.

    Derived from the material's name alone, never from a component or layer
    path, so every component of one material shares it wherever it sits. It
    is an identity, like the per-component distinction colour, and claims
    nothing about how the material looks.
    """

    return _layer_color(f"material:{material}")


def _material_color(
    material: str,
    material_colors: Mapping[str, tuple[int, int, int]] | None,
) -> tuple[int, int, int]:
    """The one colour a declared material wears: its declared colour, else its identity colour."""

    declared = (material_colors or {}).get(material)
    return declared if declared is not None else _material_identity_color(material)


def declared_material(
    components: Iterable[str],
    material_by_component: Mapping[str, str] | None,
    *,
    object_name: str | None = None,
    material_by_part: Mapping[str, Mapping[str, str | None]] | None = None,
) -> str | None:
    """The material an object of these components declares, or None when none of them declares one.

    Each component gives the object one material or none, part first (#580):
    a component listed in ``material_by_part`` gives it what the part that
    delivered it wears - matched by the object's name among all of that
    component's parts (``part_of_object``), the part's own material, else the
    component's, else none - and any other object of it, like any object of
    a component not listed there, the component's ``material_by_component``
    entry. Those values, each once, sorted and joined with ``,``: the
    ``archflow:material`` text an export writes on the object and the name of
    the one native material it wears. A component that gives nothing adds
    nothing, so an object is undeclared only when none of its components
    gives it a material.
    """

    materials: set[str] = set()
    for component in components:
        parts = (material_by_part or {}).get(component)
        part = part_of_object(object_name, parts) if parts else None
        material = parts[part] if part is not None else (material_by_component or {}).get(component)
        if material is not None:
            materials.add(material)
    return ",".join(sorted(materials)) if materials else None


def _rgb(value: object, field: str) -> tuple[int, int, int]:
    if not isinstance(value, tuple) or len(value) != 3:
        raise CadTranslationError(f"{field} must be a three-channel tuple")
    channels: list[int] = []
    for channel in value:
        if isinstance(channel, bool) or not isinstance(channel, int):
            raise CadTranslationError(f"{field} channels must be integers")
        if channel < 0 or channel > 255:
            raise CadTranslationError(
                f"{field} channels must be between 0 and 255"
            )
        channels.append(channel)
    return channels[0], channels[1], channels[2]


def _resolved_layer_colors(
    layer_paths: set[str],
    *,
    material_by_component: Mapping[str, str] | None,
    material_colors: Mapping[str, tuple[int, int, int]] | None,
) -> tuple[tuple[str, tuple[int, int, int]], ...]:
    """Resolve the one color table used by both the script and its contract.

    A component layer whose components all declare one material wears that
    material's colour (``_material_color``: declared, else the identity of
    its name), so components of one material share a colour. Any other layer
    - a component with no declared material, components declaring different
    materials, the root - keeps the deterministic path-derived distinction
    colour, which names no material.
    """

    rows: list[tuple[str, tuple[int, int, int]]] = []
    for layer_path in sorted({_ROOT_LAYER, *layer_paths}):
        color = _layer_color(layer_path)
        if layer_path != _ROOT_LAYER:
            components = layer_path.split("::", 1)[1].split("+")
            materials = {(material_by_component or {}).get(component) for component in components}
            if len(materials) == 1 and None not in materials:
                color = _material_color(materials.pop(), material_colors)
        rows.append(
            (
                layer_path,
                _rgb(color, f"layer color for {layer_path}"),
            )
        )
    return tuple(rows)


def expected_object_semantics(
    program,
    *,
    material_by_component: Mapping[str, str] | None = None,
    material_by_part: Mapping[str, Mapping[str, str | None]] | None = None,
    layer_by_component: Mapping[str, str] | None = None,
) -> dict[str, dict]:
    """The semantics each physical object must carry in the CAD document.

    Derived from the program alone: producer op, binding ids, component
    id, commitment and evidence refs, the per-component layer path, and
    every one of the operation's own ``statements`` — what a saved solid
    cannot show about itself — written through verbatim as
    ``archflow:<key>``. A statement that names a reserved identity key
    fails ``CadTranslationError``. Objects the program leaves unbound stay
    on the root layer with no invented component. A bound object names the
    material its components declare (``declared_material``: its part's in
    ``material_by_part`` first, else its component's in
    ``material_by_component``), or says ``archflow:material_status`` =
    ``undeclared`` when none of them declares one. Families report the block
    definitions arrays must create with their instance multiplicities.
    """

    proposal = program.proposal
    bindings = {
        binding.binding_id: binding
        for binding in getattr(proposal, "semantic_bindings", ())
    }
    objects: dict[str, dict] = {}
    families: dict[str, int] = {}
    for operation in proposal.operations:
        kind = operation.kind.value
        parameters = operation_parameters(operation)
        for object_id in operation.output_object_ids:
            binding_ids = tuple(
                sorted(getattr(operation, "semantic_binding_ids", ()) or ())
            )
            components = sorted(
                {
                    bindings[item].component_id
                    for item in binding_ids
                    if item in bindings
                }
            )
            commitments = sorted(
                {
                    ref
                    for item in binding_ids
                    if item in bindings
                    for ref in bindings[item].commitment_refs
                }
            )
            evidence = sorted(
                {
                    ref
                    for item in binding_ids
                    if item in bindings
                    for ref in bindings[item].evidence_refs
                }
            )
            layer = _component_layer(components, layer_by_component)
            user_text = {
                "archflow:producer_op": operation.op_id,
                "archflow:object_ref": f"cad-object:{object_id}",
                "archflow:operation_ref": f"cad-operation:{operation.op_id}",
            }
            if binding_ids:
                user_text["archflow:bindings"] = ",".join(binding_ids)
            if components:
                user_text["archflow:component"] = "+".join(components)
                material = declared_material(components, material_by_component, object_name=object_id,
                                             material_by_part=material_by_part)
                if material:
                    user_text["archflow:material"] = material
                else:
                    # A component that declares no material is said to, and
                    # never given one: no native material, no guessed name.
                    user_text["archflow:material_status"] = UNDECLARED_MATERIAL
            if commitments:
                user_text["archflow:commitments"] = ",".join(commitments)
            if evidence:
                user_text["archflow:evidence"] = ",".join(evidence)
            for key, statement in sorted(operation.statements.items()):
                if key in _RESERVED_USER_TEXT:
                    raise CadTranslationError(
                        f"statement {key!r} on {operation.op_id} would "
                        "overwrite an identity string the export owns"
                    )
                user_text[f"archflow:{key}"] = statement
            objects[object_id] = {
                "name": object_id,
                "layer": layer,
                "user_text": user_text,
            }
            if bool(parameters.get("hidden_for_inspection", False)):
                objects[object_id]["visible"] = False
                user_text["archflow:inspection_witness"] = "hidden"
        if kind in ("array", "radial_array"):
            families[f"archflow-family-{operation.op_id}"] = int(
                parameters["count"]
            )
    physical = set(delivered_object_ids(proposal))
    return {
        "objects": {
            object_id: row
            for object_id, row in sorted(objects.items())
            if object_id in physical
        },
        "blocks": families,
    }
