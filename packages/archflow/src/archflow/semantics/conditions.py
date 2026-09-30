"""Conditions: the spatial condition a component or connection forms. Canonical ids
are ``condition.<name>``.

A condition is not an object: a threshold may be a door, a portico, or eight
metres of continuous space between two regions. It is held by whatever entity
forms it, and one geometry may form several.
"""

from __future__ import annotations

from archflow.semantics.roles import SemanticTerm

CONDITIONS: tuple[SemanticTerm, ...] = (
    SemanticTerm("condition.threshold", "a transition between two regions that access passes through", ("threshold", "entry", "controlled-entry", "arrival", "门槛", "过渡")),
    SemanticTerm("condition.interface", "where two elements, levels or masses meet and load or weather crosses", ("interface", "junction", "transition", "交接")),
    SemanticTerm("condition.edge", "the edge of a mass or level", ("edge", "边缘")),
    SemanticTerm("condition.void", "an opening cut through a host", ("void", "host-void", "opening", "洞口")),
    SemanticTerm("condition.clearance", "free space that must remain between two zones or elements", ("clearance", "净空", "净距")),
    SemanticTerm("condition.axis", "an axis that elements align to or a route follows", ("axis", "axial", "轴线")),
    SemanticTerm("condition.light_transition", "where daylight enters, changes or terminates", ("light-transition", "light-termination", "采光过渡")),
    SemanticTerm("condition.course", "a course line on a facade", ("course", "层线")),
)

CONDITION_IDS: frozenset[str] = frozenset(term.id for term in CONDITIONS)
