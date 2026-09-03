# ADR-006 — Architectural semantics compile to registered ids

**Decision (2026-09-03):** a State Record's semantic fields name ids from `archflow/semantics/`
(`role.*`, `condition.*`), or a registered alias or compound phrase that resolves to them. A
string that resolves to nothing is refused by the record with the nearest ids named. Entity,
role and condition are three things: a wall is an entity, weather enclosure is a role, a
threshold is a condition, and one geometry may hold several of each.

**Why:** the villa record carried 41 components with 41 distinct `semantic_kind` phrases and
no shared vocabulary; every project would reinvent its own words, as every lane reinvented
its own modules.

**Do not:** add a semantic string to canonical state that is not in the registry; fold entity,
role and condition into one class hierarchy; add a term without saying why existing terms
cannot be composed to express it.
