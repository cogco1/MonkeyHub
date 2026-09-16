"""Where each retained record keeps a project-version identity, said by its owner.

A format migration changes what ``ProjectVersionRef.state_sha256`` means, so
every retained record that embeds one has to be restated. Which field that is
belongs to the record's owner, not to the migrator: guessing from JSON shape is
how a migration silently rewrites something it does not understand.

Each owner module declares its own contracts in module-level constants and
registers them here at import; :mod:`archflow.project.version_ref_owners` is
the one place that loads every owner, so no reader depends on having imported
the right module by accident. This module holds the table and applies a
declaration; it interprets no payload of its own.

Four things can be declared.

``VERSION_REF_POINTERS``
    schema literal -> pointers at which that record keeps a version identity.
    A pointer names the *digest-bearing field*: ``"/base"`` for the exact
    ``{project_id, version, state_sha256}`` mapping or the two-field
    ``{version, state_sha256}`` form, ``"/metadata/base_state_sha256"`` for a
    flat scalar. Pointers are relative to the record that declares them, and a
    declaration applies wherever that schema appears - nested inside another
    record included - so each owner states only its own structure.

``register_structural``
    a keyed sub-object with no schema literal of its own (a ``RunRef``, a
    design ``BranchRef``) that carries a version identity wherever it is
    embedded. Declared once by the module that defines the shape.

``register_content_digest``
    how to compute the *content digest of one record*, so that when the record
    is restated every other record that cites that digest can be moved with
    it. The owner computes it; nothing here knows what a digest covers.

``register_derived``
    which serialised fields of a record are derived from its own contents, and
    how the owner rebuilds them. Every declared kind states this, ``()`` when
    nothing is derived, so a record that carries a self-derived digest can
    never pass silently for want of a registration: without the rebuild such a
    field keeps a value derived from the pre-migration base, the record's own
    ``from_dict`` then refuses it, and the migration reports success on a
    project that cannot open its next stage.

A location no owner declares is not migrated: the planner reports it and the
migration refuses. Silence is never read as absence.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Callable


class VersionRefDeclarationError(ValueError):
    """A declaration this module cannot apply, or a conflicting registration."""


# What a declared location holds.
VERSION_REF = "version_ref"
TWO_KEY = "version_digest"
DIGEST_FIELD = "digest_field"

_VERSION_REF_KEYS = frozenset({"project_id", "version", "state_sha256"})
_TWO_KEY_KEYS = frozenset({"version", "state_sha256"})

_DECLARATIONS: dict[str, tuple[str, ...]] = {}
_STRUCTURAL: dict[frozenset[str], str] = {}
_CONTENT_DIGESTS: dict[str, Callable[[Mapping[str, Any]], str]] = {}
_DERIVED: dict[str, tuple[tuple[str, ...], Callable[[Mapping[str, Any]], Mapping[str, Any]] | None]] = {}


# ---------------------------------------------------------------- registration

def register(schema_pointers: Mapping[str, tuple[str, ...]]) -> None:
    """Register one owner module's pointer declarations. Idempotent per schema.

    Re-registering a schema with the same pointers is what a reimported module
    does; registering it with different pointers is two owners disagreeing
    about one contract, and is refused rather than resolved.
    """

    for schema, pointers in schema_pointers.items():
        declared = tuple(pointers)
        for pointer in declared:
            _tokens(pointer)
        existing = _DECLARATIONS.get(schema)
        if existing is not None and existing != declared:
            raise VersionRefDeclarationError(
                f"schema {schema!r} is already declared with different pointers: "
                f"{existing} vs {declared}"
            )
        _DECLARATIONS[schema] = declared


def register_structural(keys: tuple[str, ...], child: str) -> None:
    """Declare a keyed sub-object whose ``child`` field holds a version identity."""

    existing = _STRUCTURAL.get(frozenset(keys))
    if existing is not None and existing != child:
        raise VersionRefDeclarationError(
            f"structural shape {sorted(keys)} is already declared at {existing!r}"
        )
    _STRUCTURAL[frozenset(keys)] = child


def register_content_digest(
    schema: str, compute: Callable[[Mapping[str, Any]], str],
) -> None:
    """Declare how to compute the content digest of one retained record."""

    existing = _CONTENT_DIGESTS.get(schema)
    if existing is not None and existing is not compute:
        raise VersionRefDeclarationError(
            f"schema {schema!r} already declares a content digest"
        )
    _CONTENT_DIGESTS[schema] = compute


def register_derived(
    schema: str,
    fields: tuple[str, ...],
    rebuild: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
) -> None:
    """Declare which serialised fields of ``schema`` derive from its own contents.

    ``fields`` may be empty, which is a statement in itself: this record keeps
    nothing that a restatement invalidates. Naming a field without saying how
    to rebuild it is refused, because that is exactly the state in which a
    migration writes a record its own reader will reject.
    """

    declared = tuple(fields)
    if declared and rebuild is None:
        raise VersionRefDeclarationError(
            f"schema {schema!r} names self-derived fields {declared} but no rebuild"
        )
    existing = _DERIVED.get(schema)
    if existing is not None and existing[0] != declared:
        raise VersionRefDeclarationError(
            f"schema {schema!r} already declares self-derived fields {existing[0]}"
        )
    _DERIVED[schema] = (declared, rebuild)


def derived_fields(schema: object) -> tuple[str, ...] | None:
    """The self-derived fields ``schema`` declares, or ``None`` if it declares none."""

    if not isinstance(schema, str):
        return None
    found = _DERIVED.get(schema)
    return None if found is None else found[0]


# ---------------------------------------------------------------- reading the table

def declared_pointers(schema: object) -> tuple[str, ...] | None:
    """The pointers ``schema`` declares, or ``None`` if no owner declares it."""

    if not isinstance(schema, str):
        return None
    return _DECLARATIONS.get(schema)


def declared_schemas() -> tuple[str, ...]:
    """Every schema literal an owner module has declared, sorted."""

    return tuple(sorted(_DECLARATIONS))


def content_digest_of(payload: Mapping[str, Any]) -> str | None:
    """The record's own content digest, if its owner declares how to compute one.

    A payload whose own owner cannot read it is not a missing digest, it is a
    record nothing can restate: swallowing that would leave every citation of
    the digest stale with nothing said about it, so it is raised with the
    schema named.
    """

    schema = payload.get("schema")
    compute = _CONTENT_DIGESTS.get(schema)
    if compute is None:
        return None
    try:
        return compute(payload)
    except Exception as exc:
        raise VersionRefDeclarationError(
            f"{schema!r} declares a content digest its owner cannot compute "
            f"from this payload: {exc}"
        ) from exc


def recompute(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    """Let the payload's owner rebuild any field derived from its own contents."""

    found = _DERIVED.get(payload.get("schema"))
    if found is None or found[1] is None:
        return payload
    return found[1](payload)


def recomputable_schemas() -> tuple[str, ...]:
    """Schemas whose owner rebuilds derived fields after a restatement."""

    return tuple(sorted(
        schema for schema, (fields, _) in _DERIVED.items() if fields
    ))


def schemas_declaring_derived_fields() -> tuple[str, ...]:
    """Every schema that has stated what it derives, empty statement included."""

    return tuple(sorted(_DERIVED))


def structural_child(payload: Mapping[str, Any]) -> str | None:
    """The field of ``payload`` holding a version identity, if it is a declared shape."""

    return _STRUCTURAL.get(frozenset(payload))


# ---------------------------------------------------------------- applying it

def _tokens(pointer: str) -> tuple[str, ...]:
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        raise VersionRefDeclarationError(
            f"a version-ref pointer must start with '/': {pointer!r}"
        )
    parts = pointer[1:].split("/")
    if any(part == "" for part in parts):
        raise VersionRefDeclarationError(f"empty pointer token in {pointer!r}")
    return tuple(
        part.replace("~1", "/").replace("~0", "~") for part in parts
    )


def _escape(key: object) -> str:
    return str(key).replace("~", "~0").replace("/", "~1")


def _walk(payload: Any, tokens: tuple[str, ...]) -> Any:
    current = payload
    for token in tokens:
        if isinstance(current, Mapping):
            if token not in current:
                return None
            current = current[token]
        elif isinstance(current, list):
            try:
                current = current[int(token)]
            except (ValueError, IndexError):
                return None
        else:
            return None
    return current


def _read_location(value: Any) -> tuple[str, str] | None:
    """(kind, digest) for a value sitting at a declared location, or None."""

    if isinstance(value, str):
        return (DIGEST_FIELD, value)
    if not isinstance(value, Mapping):
        return None
    digest = value.get("state_sha256")
    if not isinstance(digest, str):
        return None
    keys = set(value)
    if keys == _VERSION_REF_KEYS:
        return (VERSION_REF, digest)
    if keys == _TWO_KEY_KEYS:
        return (TWO_KEY, digest)
    return None


def locations(payload: Any, pointer: str = "") -> list[tuple[str, str, str]]:
    """(canonical pointer, kind, digest) for every declared location in ``payload``.

    The walk descends into nested records: a payload that carries its own
    ``schema`` literal has that schema's declaration applied at its own
    position, so ``SelectedSchematicInput@1`` inside a
    ``DevelopedDesignState@1`` is covered by the input's own declaration and
    neither owner has to know about the other. Structural shapes are covered
    wherever they appear. Pointers come back in the same spelling a generic
    scan produces, so the two can be compared directly.
    """

    found: list[tuple[str, str, str]] = []
    if isinstance(payload, Mapping):
        for declared in declared_pointers(payload.get("schema")) or ():
            tokens = _tokens(declared)
            read = _read_location(_walk(payload, tokens))
            if read is not None:
                kind, digest = read
                found.append((
                    pointer + "".join(f"/{_escape(token)}" for token in tokens),
                    kind, digest,
                ))
        child = structural_child(payload)
        if child is not None:
            read = _read_location(payload.get(child))
            if read is not None:
                kind, digest = read
                found.append((f"{pointer}/{_escape(child)}", kind, digest))
        for key, value in payload.items():
            found.extend(locations(value, f"{pointer}/{_escape(key)}"))
    elif isinstance(payload, list):
        for index, value in enumerate(payload):
            found.extend(locations(value, f"{pointer}/{index}"))
    seen: set[str] = set()
    unique: list[tuple[str, str, str]] = []
    for row in found:
        if row[0] in seen:
            continue
        seen.add(row[0])
        unique.append(row)
    return unique


def covered_pointers(payload: Any) -> set[str]:
    """Every pointer in ``payload`` that some owner declares. One rule, one spelling."""

    return {pointer for pointer, _, _ in locations(payload)}


def restate(payload: Mapping[str, Any], mapping: Mapping[str, str]) -> dict[str, Any]:
    """A copy of ``payload`` with every declared digest replaced through ``mapping``.

    Only declared locations are touched, and only where the digest already
    there is one ``mapping`` knows; a location holding an unknown digest is
    left as it is and the caller decides whether that is a blocker. After the
    substitution each owner is given the chance to rebuild the fields it
    derives from its own contents, innermost first.
    """

    result = _restate_value(payload, mapping)
    if not isinstance(result, dict):  # pragma: no cover - a mapping stays a mapping
        raise VersionRefDeclarationError("a record payload must be a mapping")
    return result


def _restate_value(value: Any, mapping: Mapping[str, str]) -> Any:
    if isinstance(value, Mapping):
        # Innermost first: an owner rebuilding a derived field must see its
        # children already restated.
        result = {key: _restate_value(item, mapping) for key, item in value.items()}
        for declared in declared_pointers(result.get("schema")) or ():
            _substitute(result, _tokens(declared), mapping)
        child = structural_child(result)
        if child is not None:
            _substitute(result, (child,), mapping)
        return dict(recompute(result))
    if isinstance(value, list):
        return [_restate_value(item, mapping) for item in value]
    return value


def _substitute(
    payload: dict[str, Any], tokens: tuple[str, ...], mapping: Mapping[str, str],
) -> None:
    parent = _walk(payload, tokens[:-1]) if len(tokens) > 1 else payload
    if not isinstance(parent, dict):
        return
    field = tokens[-1]
    value = parent.get(field)
    if isinstance(value, str):
        if value in mapping:
            parent[field] = mapping[value]
        return
    if not isinstance(value, dict):
        return
    digest = value.get("state_sha256")
    if (
        isinstance(digest, str)
        and digest in mapping
        and set(value) in (_VERSION_REF_KEYS, _TWO_KEY_KEYS)
    ):
        value["state_sha256"] = mapping[digest]
