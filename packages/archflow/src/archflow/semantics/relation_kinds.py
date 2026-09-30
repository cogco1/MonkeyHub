"""Relation kinds: what a State Record relation between two entities is called.

A kind names the relation and nothing more. Its subject and object, the datum
role it binds, what a change propagates and how it is checked belong to the
record's ``Relation`` (``archflow.state.state_record``), which accepts these
kinds and no other.
"""

from __future__ import annotations

from enum import StrEnum


class ArchitecturalRelationKind(StrEnum):
    """Core relation vocabulary; the relation naming a kind carries its endpoints."""

    COMPOSITION = "composition"
    AGGREGATES = "aggregates"
    PRIMARY_CONTAINS = "primary_contains"
    REFERENCES_ZONE = "references_zone"
    ADJACENT = "adjacent"
    INTERSECTS = "intersects"
    DEPENDENCY = "dependency"
    SUPPORT = "support"
    LOAD_TRANSFER = "load_transfer"
    HOST = "host"
    HOSTS_VOID = "hosts_void"
    FILLS_VOID = "fills_void"
    ACCESS = "access"
    ALLOWS_PASSAGE = "allows_passage"
    CLEARANCE = "clearance"
    REALIZATION = "realization"
    REALIZES = "realizes"
    LINEAGE = "lineage"
    REFINES = "refines"
    REPLACES = "replaces"
    INTERFACE = "interface"
    ALIGNMENT = "alignment"
    SYMMETRIC_WITH = "symmetric_with"
    BLOCKS = "blocks"
    EVIDENCES = "evidences"
