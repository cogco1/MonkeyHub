"""The portfolio lane's coordinated developed-design state.

``test_geometry_compiler.py`` came home to the spine suite, and its state
is now the spine's: an authored ``StateRecord@1`` projected by
``developed_design_view`` (``tests/support.py``). That projection carries
no ``DevelopedComponent`` developments, because the spine has no
development ceremony — ``initialize_developed_design`` and
``compile_development_step`` left with this lane.

The lane's own tests read those developments, so they keep the state the
lane's constructor builds and take it from here. ``_state`` is the
function that used to live at the top of ``test_geometry_compiler.py``,
unchanged.
"""

from __future__ import annotations

from dataclasses import replace

from archflow.state.developed_design import DevelopedDesignState
from archive.tests.test_design_development import _coordinated_state


def _state(*, width: float = 6.0) -> DevelopedDesignState:
    state = _coordinated_state()[3]
    if width == 6.0:
        return state
    proposal = state.selected_schematic.option.proposal
    components = tuple(
        replace(
            item,
            revision=item.revision + 1,
            intent=f"{item.intent} Width decision {width}.",
        )
        if item.component_id == "building"
        else item
        for item in proposal.components
    )
    option = replace(
        state.selected_schematic.option,
        proposal=replace(proposal, components=components),
    )
    return replace(
        state,
        selected_schematic=replace(
            state.selected_schematic,
            option=option,
        ),
    )
