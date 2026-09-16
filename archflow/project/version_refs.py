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

``register_reader``
    the owner's own reader for a record, used to check a migrated one back. A
    rebuild registered beside it cannot vouch for itself: comparing a rebuilt
    field with the same rebuild proves only that the function is a function.
    The reader is what every later stage will use, so it is what decides.

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
# Shapes that name a *file* by digest rather than a version. Their digest is
# covered - the caller moves it with the file - but it is never restated as a
# version identity: in format 1 the two are the same string and only the
# shape says which is meant.
_FILE_REFERENCES: dict[frozenset[str], str] = {}
_CONTENT_DIGESTS: dict[str, Callable[[Mapping[str, Any]], str]] = {}
_DERIVED: dict[str, tuple[tuple[str, ...], Callable[[Mapping[str, Any]], Mapping[str, Any]] | None]] = {}
_READERS: dict[str, Callable[[Mapping[str, Any]], Any]] = {}
_CLOSURE_CHECKS: dict[str, Callable[..., None]] = {}


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


def register_reader(schema: str, read: Callable[[Mapping[str, Any]], Any]) -> None:
    """Declare the owner's reader, which a migrated record has to satisfy."""

    existing = _READERS.get(schema)
    if existing is not None and existing is not read:
        raise VersionRefDeclarationError(
            f"schema {schema!r} already declares a reader"
        )
    _READERS[schema] = read


def register_closure_check(schema: str, check: Callable[..., None]) -> None:
    """Declare an owner's rule that can only be checked against other records.

    A record may cite another record's content digest. Nothing inside the
    citing record can tell whether that value is still right - only the record
    it names can - so the owner is given a resolver and states the rule
    itself. This is deliberately not routed through the content-digest
    registration: an owner that miscomputes its own digest would otherwise
    agree with itself everywhere, and a migration would write a project whose
    bindings only fail at the next stage.
    """

    existing = _CLOSURE_CHECKS.get(schema)
    if existing is not None and existing is not check:
        raise VersionRefDeclarationError(
            f"schema {schema!r} already declares a closure check"
        )
    _CLOSURE_CHECKS[schema] = check


def closure_check(
    payload: Mapping[str, Any], resolve: Callable[[Any], Mapping[str, Any] | None],
) -> None:
    """Run the owner's cross-record rule for this payload, if it declares one."""

    check = _CLOSURE_CHECKS.get(payload.get("schema"))
    if check is not None:
        check(payload, resolve)


def schemas_declaring_closure_checks() -> tuple[str, ...]:
    """Every schema whose owner states a rule about the records it cites."""

    return tuple(sorted(_CLOSURE_CHECKS))


def read_back(payload: Mapping[str, Any]) -> None:
    """Put a payload through its owner's reader. Raises what that reader raises."""

    read = _READERS.get(payload.get("schema"))
    if read is not None:
        read(payload)


def schemas_declaring_readers() -> tuple[str, ...]:
    """Every schema whose owner lends its reader to the migration's check."""

    return tuple(sorted(_READERS))


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


def register_file_reference(keys: tuple[str, ...], child: str) -> None:
    """Declare a shape whose ``child`` names a retained file by its digest."""

    existing = _FILE_REFERENCES.get(frozenset(keys))
    if existing is not None and existing != child:
        raise VersionRefDeclarationError(
            f"file-reference shape {sorted(keys)} is already declared at {existing!r}"
        )
    _FILE_REFERENCES[frozenset(keys)] = child


def file_reference_child(payload: Mapping[str, Any]) -> str | None:
    """The field of ``payload`` naming a file by digest, if it is a declared shape."""

    return _FILE_REFERENCES.get(frozenset(payload))


def file_reference_locations(payload: Any, pointer: str = "") -> list[str]:
    """Every pointer inside ``payload`` that names a retained file by digest.

    These are covered, so they do not block; the migration moves them with the
    files they name rather than restating them as versions.
    """

    found: list[str] = []
    if isinstance(payload, Mapping):
        child = file_reference_child(payload)
        for key, value in payload.items():
            token = _escape(key)
            # The whole reference is accounted for, not only its digest field:
            # a content-addressed file carries its digest in its name too, so
            # the path and the uri hold it as well.
            if child is not None and isinstance(value, str):
                found.append(f"{pointer}/{token}")
                continue
            found.extend(file_reference_locations(value, f"{pointer}/{token}"))
    elif isinstance(payload, list):
        for index, value in enumerate(payload):
            found.extend(file_reference_locations(value, f"{pointer}/{index}"))
    return found


def structural_child(payload: Mapping[str, Any]) -> str | None:
    """The field of ``payload`` holding a version identity, if it is a declared shape."""

    return _STRUCTURAL.get(frozenset(payload))


# ---------------------------------------------------------------- applying it

def _tokens(pointer: str) -> tuple[Any, ...]:
    """The tokens of one declaration, a keyed-row selector included.

    ``/document_user_strings[key=archflow:base_state_sha256]/value`` names the
    ``value`` of whichever row of that list carries that key. A CAD document's
    user strings are a sorted list of key/value rows, so the row a record means
    cannot be named by index: its position moves with the data.
    """

    if not isinstance(pointer, str) or not pointer.startswith("/"):
        raise VersionRefDeclarationError(
            f"a version-ref pointer must start with '/': {pointer!r}"
        )
    parts = pointer[1:].split("/")
    if any(part == "" for part in parts):
        raise VersionRefDeclarationError(f"empty pointer token in {pointer!r}")
    tokens: list[Any] = []
    for part in parts:
        part = part.replace("~1", "/").replace("~0", "~")
        if part.endswith("]") and "[" in part:
            field, predicate = part[:-1].split("[", 1)
            if "=" not in predicate or not field:
                raise VersionRefDeclarationError(
                    f"a keyed-row selector reads as name[key=value]: {pointer!r}"
                )
            key, value = predicate.split("=", 1)
            tokens.append((field, key, value))
            continue
        tokens.append(part)
    return tuple(tokens)


def _select(parent: Any, token: Any) -> tuple[Any, str | None]:
    """(value, canonical token) for one declaration token applied to ``parent``."""

    if isinstance(token, tuple):
        field, key, wanted = token
        rows = parent.get(field) if isinstance(parent, Mapping) else None
        if not isinstance(rows, list):
            return None, None
        for index, row in enumerate(rows):
            if isinstance(row, Mapping) and row.get(key) == wanted:
                return row, f"{_escape(field)}/{index}"
        return None, None
    if isinstance(parent, Mapping):
        return (parent.get(token), _escape(token)) if token in parent else (None, None)
    if isinstance(parent, list):
        try:
            return parent[int(token)], _escape(token)
        except (ValueError, IndexError):
            return None, None
    return None, None


def _resolve(payload: Any, tokens: tuple[Any, ...]) -> tuple[Any, str | None]:
    """Walk a declaration to its value, and to the plain pointer that names it."""

    current, spelling = payload, ""
    for token in tokens:
        current, step = _select(current, token)
        if step is None:
            return None, None
        spelling = f"{spelling}/{step}"
    return current, spelling


def _escape(key: object) -> str:
    return str(key).replace("~", "~0").replace("/", "~1")


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
            value, spelling = _resolve(payload, _tokens(declared))
            read = _read_location(value)
            if read is not None and spelling is not None:
                kind, digest = read
                found.append((pointer + spelling, kind, digest))
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
    """Every pointer in ``payload`` some owner accounts for. One rule, one spelling.

    A declared version identity, and a declared file reference: the second is
    not restated as a version but it is not unaccounted for either, and in
    format 1 a file digest and a version digest are the same string.
    """

    return (
        {pointer for pointer, _, _ in locations(payload)}
        | set(file_reference_locations(payload))
    )


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
    payload: dict[str, Any], tokens: tuple[Any, ...], mapping: Mapping[str, str],
) -> None:
    parent: Any = payload
    for token in tokens[:-1]:
        parent, step = _select(parent, token)
        if step is None:
            return
    if not isinstance(parent, dict):
        return
    field = tokens[-1]
    if isinstance(field, tuple):  # pragma: no cover - a row is not itself a digest
        return
    value = parent.get(field)
    if isinstance(value, str):
        replaced = _replace_digests(value, mapping)
        if replaced != value:
            parent[field] = replaced
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


def _replace_digests(value: str, mapping: Mapping[str, str]) -> str:
    """Replace every mapped digest inside ``value``, whatever else it holds.

    A retained field may hold the digest alone, or wrapped: ``record:<sha>``,
    ``artifact:sha256:<sha>``, a file name, a ``project://`` URI. Matching is
    case-insensitive because a CAD document may have upper-cased it on the way
    through; the replacement is written in the canonical lower case.
    """

    if len(value) < 64:
        return value
    lowered = value.lower()
    for digest, replacement in mapping.items():
        at = lowered.find(digest.lower())
        while at != -1:
            value = value[:at] + replacement + value[at + len(digest):]
            lowered = value.lower()
            at = lowered.find(digest.lower(), at + len(replacement))
    return value
