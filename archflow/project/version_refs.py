"""Where each retained record schema keeps a project-version identity.

A format migration changes what ``ProjectVersionRef.state_sha256`` means, so
every retained record that embeds one has to be restated. Which field that is
belongs to the record's owner, not to the migrator: guessing from JSON shape
is how a migration silently rewrites something it does not understand.

So each owner module declares its own locations in a module-level
``VERSION_REF_POINTERS`` mapping of schema literal to pointer declarations, and
registers it here at import. This module holds the table and the one generic
restater that applies a declaration; it interprets no payload of its own.

A declaration names one of four retained spellings:

``"/base"``
    the exact ``{project_id, version, state_sha256}`` mapping at that pointer.
``"/branch/base"`` with ``TWO_KEY``
    the ``{version, state_sha256}`` mapping a design branch retains.
``"/metadata/base_version+base_state_sha256"``
    the split scalar pair a CAD receipt writes into flat metadata: the ``+``
    separates two sibling field names under the same parent pointer.
``"/state_record_digest"`` with ``CONTENT_DIGEST``
    not a project version at all but a *record content* digest, which moves
    when the record it names is restated. It is declared so the cascade knows
    to recompute it rather than leaving a stale digest behind.

A location a schema does not declare is not migrated: the planner reports it
as a blocker and the migration refuses. Silence is never taken for absence.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class VersionRefDeclarationError(ValueError):
    """A declaration this module cannot apply, or a conflicting registration."""


# What a declared pointer holds.
VERSION_REF = "version_ref"
TWO_KEY = "version_digest"
SPLIT_SCALAR = "split_scalar"
CONTENT_DIGEST = "content_digest"

_VERSION_REF_KEYS = frozenset({"project_id", "version", "state_sha256"})
_TWO_KEY_KEYS = frozenset({"version", "state_sha256"})

_DECLARATIONS: dict[str, tuple[str, ...]] = {}
# Typed sub-objects that carry a version identity wherever they are embedded.
# A ``RunRef`` is one: no schema literal of its own, an exact key set, and one
# child that holds the base. Its owner registers the shape once and every
# record that embeds it is covered, without any record having to know.
_STRUCTURAL: dict[frozenset[str], str] = {}


def register_structural(keys: tuple[str, ...], child: str) -> None:
    """Declare a keyed sub-object whose ``child`` field holds a version identity."""

    existing = _STRUCTURAL.get(frozenset(keys))
    if existing is not None and existing != child:
        raise VersionRefDeclarationError(
            f"structural shape {sorted(keys)} is already declared at {existing!r}"
        )
    _STRUCTURAL[frozenset(keys)] = child


def structural_child(payload: Mapping[str, Any]) -> str | None:
    """The field of ``payload`` that holds a version identity, if it is a declared shape."""

    return _STRUCTURAL.get(frozenset(payload))


def structural_locations(payload: Any, pointer: str = "") -> list[str]:
    """Every pointer inside ``payload`` that a registered structural shape covers."""

    found: list[str] = []
    if isinstance(payload, Mapping):
        child = structural_child(payload)
        for key, value in payload.items():
            token = str(key).replace("~", "~0").replace("/", "~1")
            if child is not None and key == child:
                found.append(f"{pointer}/{token}")
                continue
            found.extend(structural_locations(value, f"{pointer}/{token}"))
    elif isinstance(payload, list):
        for index, value in enumerate(payload):
            found.extend(structural_locations(value, f"{pointer}/{index}"))
    return found


def _restate_structural(value: Any, mapping: Mapping[str, str]) -> Any:
    """Rewrite every declared structural sub-object, wherever it is embedded."""

    if isinstance(value, Mapping):
        child = structural_child(value)
        result = {}
        for key, item in value.items():
            if child is not None and key == child and isinstance(item, Mapping):
                digest = item.get("state_sha256")
                if (
                    isinstance(digest, str)
                    and digest in mapping
                    and set(item) in (_VERSION_REF_KEYS, _TWO_KEY_KEYS)
                ):
                    result[key] = {**item, "state_sha256": mapping[digest]}
                    continue
            result[key] = _restate_structural(item, mapping)
        return result
    if isinstance(value, list):
        return [_restate_structural(item, mapping) for item in value]
    return value


def register(schema_pointers: Mapping[str, tuple[str, ...]]) -> None:
    """Register one owner module's declarations. Idempotent per schema.

    Re-registering the same schema with the same pointers is what a reimported
    module does; registering it with different pointers is two owners
    disagreeing about one contract and is refused.
    """

    for schema, pointers in schema_pointers.items():
        declared = tuple(pointers)
        for pointer in declared:
            _parse(pointer)
        existing = _DECLARATIONS.get(schema)
        if existing is not None and existing != declared:
            raise VersionRefDeclarationError(
                f"schema {schema!r} is already declared with different pointers: "
                f"{existing} vs {declared}"
            )
        _DECLARATIONS[schema] = declared


def declared_pointers(schema: object) -> tuple[str, ...] | None:
    """The pointers ``schema`` declares, or ``None`` if no owner declares it."""

    if not isinstance(schema, str):
        return None
    return _DECLARATIONS.get(schema)


def declared_schemas() -> tuple[str, ...]:
    """Every schema literal an owner module has declared, sorted."""

    return tuple(sorted(_DECLARATIONS))


def _parse(pointer: str) -> tuple[tuple[str, ...], str, tuple[str, ...]]:
    """(parent tokens, kind, field names) for one declaration."""

    if not isinstance(pointer, str) or not pointer.startswith("/"):
        raise VersionRefDeclarationError(
            f"a version-ref pointer must start with '/': {pointer!r}"
        )
    tokens = [
        token.replace("~1", "/").replace("~0", "~")
        for token in pointer[1:].split("/")
    ]
    last = tokens[-1]
    if "+" in last:
        fields = tuple(part for part in last.split("+") if part)
        if len(fields) != 2:
            raise VersionRefDeclarationError(
                f"a split-scalar pointer names exactly two sibling fields: {pointer!r}"
            )
        return tuple(tokens[:-1]), SPLIT_SCALAR, fields
    return tuple(tokens[:-1]), "", (last,)


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


def locations(payload: Mapping[str, Any], schema: object = None) -> list[tuple[str, str, str]]:
    """(pointer, kind, digest) for every declared location that holds a digest.

    A declared location that is absent or null is skipped: an owner may declare
    a nullable field, and nothing is invented for one that is not there.
    """

    declared = declared_pointers(
        schema if schema is not None else payload.get("schema")
    )
    if declared is None:
        return []
    found: list[tuple[str, str, str]] = []
    for pointer in declared:
        parent_tokens, kind, fields = _parse(pointer)
        parent = _walk(payload, parent_tokens)
        if not isinstance(parent, Mapping):
            continue
        if kind == SPLIT_SCALAR:
            digest = parent.get(fields[1])
            if isinstance(digest, str):
                found.append((pointer, SPLIT_SCALAR, digest))
            continue
        value = parent.get(fields[0])
        if isinstance(value, str):
            found.append((pointer, CONTENT_DIGEST, value))
            continue
        if not isinstance(value, Mapping):
            continue
        keys = set(value)
        digest = value.get("state_sha256")
        if not isinstance(digest, str):
            continue
        if keys == _VERSION_REF_KEYS:
            found.append((pointer, VERSION_REF, digest))
        elif keys == _TWO_KEY_KEYS:
            found.append((pointer, TWO_KEY, digest))
    return found


def restate(
    payload: Mapping[str, Any],
    mapping: Mapping[str, str],
    *,
    schema: object = None,
) -> dict[str, Any]:
    """A copy of ``payload`` with every declared digest replaced through ``mapping``.

    Only declared locations are touched, and only where the digest already
    there is one ``mapping`` knows. A location holding an unknown digest is
    left exactly as it is: the caller decides whether that is a blocker.
    """

    declared = declared_pointers(
        schema if schema is not None else payload.get("schema")
    )
    result = _restate_structural(_deep_copy(payload), mapping)
    if not declared:
        return result
    for pointer in declared:
        parent_tokens, kind, fields = _parse(pointer)
        parent = _walk(result, parent_tokens)
        if not isinstance(parent, dict):
            continue
        if kind == SPLIT_SCALAR:
            digest = parent.get(fields[1])
            if isinstance(digest, str) and digest in mapping:
                parent[fields[1]] = mapping[digest]
            continue
        value = parent.get(fields[0])
        if isinstance(value, str):
            if value in mapping:
                parent[fields[0]] = mapping[value]
            continue
        if not isinstance(value, dict):
            continue
        digest = value.get("state_sha256")
        if (
            isinstance(digest, str)
            and digest in mapping
            and set(value) in (_VERSION_REF_KEYS, _TWO_KEY_KEYS)
        ):
            value["state_sha256"] = mapping[digest]
    return result


def _deep_copy(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _deep_copy(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_deep_copy(item) for item in value]
    return value
