"""A few attacks whose outcome is the point: what a file can and cannot prove about itself (F3, 3DM)."""

from __future__ import annotations

import pytest

from labs.revision_tasks import _bootstrap  # noqa: F401
from labs.revision_tasks import attacks

try:
    from monkeycad.backends.occt.kernel import occt_available
    import rhino3dm  # noqa: F401
    READY = occt_available()
except ImportError:
    READY = False

pytestmark = pytest.mark.skipif(not READY, reason="needs the OCCT kernel (cadquery-ocp) and rhino3dm")


@pytest.mark.parametrize("arm, attack, expected", [
    # A guard removed with a recomputed digest is invisible to every in-file arm and out of reach for D.
    ("C-full", "A04_relax_guard_recomputed", "undetected_different_outcome"),
    ("D", "A04_relax_guard_recomputed", "not_applicable"),
    # Only the authority outside the file knows the older file is not the current one.
    ("C-full", "A08_replay_older_file", "undetected_different_outcome"),
    ("D", "A08_replay_older_file", "detected"),
    # A malformed record is refused when read, not partway through a revision.
    ("C-full", "A10_dependency_cycle_recomputed", "detected"),
    # Without per-object digests an outside edit is accepted, and the rebuild discards it.
    ("C-unbound", "A01_foreign_move_keep_metadata", "undetected_same_outcome"),
])
def test_attack_outcome(tmp_path, arm, attack, expected):
    row = attacks.run_case(attacks.FAMILIES["F3"], arm, attacks.ATTACKS[attack], tmp_path / "case")
    assert row["class"] == expected, row
