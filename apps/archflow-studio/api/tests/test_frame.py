"""The frame: the levels and axes everything is positioned against.

The portico record declares one ``Level@1`` (``level-ground``, elevation 0) and
one ``GridAxis@1`` (``axis-a``, role ``front``, direction (0, 0, 1), so a line
of constant world x at 0). One element names the level outright —
``portico-base`` is placed by ``{"base": {"level": "level-ground"}}`` — and two
more stand on that element's published datum. So changing the level moves the
base, and through it everything the base carries: that is what these tests
measure, and they measure it against the kernel's own closure rather than
against a list this module wrote down.

Nothing in this file writes to the project.
"""

from __future__ import annotations

from pathlib import Path
import shutil
import tempfile
import unittest

from fastapi.testclient import TestClient

from archflow.state.state_record import StateRecord

from archflow_studio_api.application.frame import closure_of_refs, frame_of
from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings
from archflow_studio_api.transport.errors import StudioError

from .support import (
    PORTICO_RECORD_PAYLOAD,
    RECORD_PAYLOAD,
    make_empty_project,
    make_portico_project,
    write_runner_record,
)

LEVEL = "level-ground"
AXIS = "axis-a"
AXIS_ROLE = "front"
BASE = "portico-base"
CORNICE = "portico-cornice"
ABUTMENT = "portico-roof-abutment-west"


class FrameTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.repository, self.state_digest = make_portico_project(self.root)
        self.client = TestClient(
            create_app(
                StudioSettings(project_dir=self.repository.layout.root)
            )
        )
        self.addCleanup(self.client.close)

    # ---- GET /api/state/frame

    def test_frame_lists_the_level_and_the_axis(self) -> None:
        body = self.client.get("/api/state/frame").json()
        self.assertEqual(
            [(row["levelId"], row["role"], row["elevation"]) for row in body["levels"]],
            [(LEVEL, "ground", 0.0)],
        )
        axis, = body["axes"]
        self.assertEqual(axis["axisId"], AXIS)
        self.assertEqual(axis["role"], AXIS_ROLE)
        # direction (0, 0, 1) is a line of constant world x at origin[0].
        self.assertEqual(axis["const"], "x")
        self.assertEqual(axis["value"], 0.0)
        self.assertEqual(axis["origin"], [0.0, 0.0, 0.0])
        self.assertEqual(axis["direction"], [0.0, 0.0, 1.0])

    def test_the_level_lists_the_element_whose_base_names_it(self) -> None:
        body = self.client.get("/api/state/frame").json()
        level, = body["levels"]
        # Only the element that names the level itself; the two that stand on
        # that element's datum are downstream, not on the level.
        self.assertEqual(level["elementsOn"], [BASE])

    def test_the_level_closure_names_what_it_would_move(self) -> None:
        body = self.client.get("/api/state/frame").json()
        level, = body["levels"]
        self.assertEqual(
            level["closure"],
            [
                f"entity:{LEVEL}",
                f"entity:{BASE}",
                f"entity:{CORNICE}",
                f"entity:{ABUTMENT}",
            ],
        )

    def test_the_frame_is_the_kernel_closure_and_not_a_second_rule(self) -> None:
        record = StateRecord.from_dict(PORTICO_RECORD_PAYLOAD)
        frame = frame_of(record)
        for level in frame.levels:
            self.assertEqual(
                level.closure, record.closure((f"entity:{level.level_id}",))
            )
        for axis in frame.axes:
            self.assertEqual(
                axis.closure, record.closure((f"entity:{axis.axis_id}",))
            )

    def test_an_axis_nothing_is_placed_against_says_so(self) -> None:
        body = self.client.get("/api/state/frame").json()
        axis, = body["axes"]
        self.assertEqual(axis["elementsOn"], [])
        self.assertIn(
            "no element references a grid axis role: the axes are declared "
            "and nothing is placed against them",
            body["honesty"],
        )

    def test_an_element_placed_on_a_grid_role_lands_on_that_axis(self) -> None:
        """An axis lists the elements that name its *role*, not its id."""

        record = StateRecord.from_dict(_placed_on_axis(PORTICO_RECORD_PAYLOAD))
        axis, = frame_of(record).axes
        self.assertEqual(axis.elements_on, (BASE,))

    def test_a_record_without_a_grid_says_so(self) -> None:
        record = StateRecord.from_dict(_without(RECORD_PAYLOAD, "GridAxis@1"))
        frame = frame_of(record)
        self.assertEqual(frame.axes, ())
        self.assertIn(
            "no GridAxis@1 in the record: nothing here places an element in "
            "plan",
            frame.honesty,
        )

    def test_a_diagonal_axis_gets_no_constant_and_the_honesty_says_why(self) -> None:
        record = StateRecord.from_dict(_diagonal_axis(PORTICO_RECORD_PAYLOAD))
        frame = frame_of(record)
        axis, = frame.axes
        self.assertIsNone(axis.const)
        self.assertIsNone(axis.value)
        self.assertEqual(axis.direction, (1.0, 0.0, 1.0))
        self.assertIn(
            f"axis {AXIS} ({AXIS_ROLE}) is parallel to neither world axis: it "
            "has no single constant this panel can show",
            frame.honesty,
        )

    # ---- POST /api/state/closure

    def test_closure_of_the_level_returns_the_elements_and_its_edges(self) -> None:
        response = self.client.post(
            "/api/state/closure",
            json={
                "stateDigest": self.state_digest,
                "changedRefs": [f"entity:{LEVEL}"],
            },
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(
            body["closure"],
            [
                f"entity:{LEVEL}",
                f"entity:{BASE}",
                f"entity:{CORNICE}",
                f"entity:{ABUTMENT}",
            ],
        )
        carried = {
            (edge["upstreamRef"], edge["downstreamRef"]) for edge in body["edges"]
        }
        self.assertIn((f"entity:{LEVEL}", f"entity:{BASE}"), carried)
        self.assertIn((f"entity:{BASE}", f"entity:{CORNICE}"), carried)
        self.assertIn((f"entity:{BASE}", f"entity:{ABUTMENT}"), carried)
        # Every edge reported has both ends inside the closure: these are the
        # edges the walk used, not every edge that touches the result.
        inside = set(body["closure"])
        for edge in body["edges"]:
            self.assertIn(edge["upstreamRef"], inside)
            self.assertIn(edge["downstreamRef"], inside)

    def test_closure_of_a_parameter_follows_the_derivations(self) -> None:
        response = self.client.post(
            "/api/state/closure",
            json={
                "stateDigest": self.state_digest,
                "changedRefs": ["parameter:module"],
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["closure"],
            ["parameter:bay", "parameter:module", "parameter:span"],
        )

    def test_a_stale_digest_is_refused(self) -> None:
        response = self.client.post(
            "/api/state/closure",
            json={"stateDigest": "f" * 64, "changedRefs": [f"entity:{LEVEL}"]},
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "STALE_BASE")

    def test_a_ref_the_record_does_not_carry_is_refused(self) -> None:
        response = self.client.post(
            "/api/state/closure",
            json={
                "stateDigest": self.state_digest,
                "changedRefs": ["entity:level-grond"],
            },
        )
        self.assertEqual(response.status_code, 422)
        body = response.json()
        self.assertEqual(body["code"], "UNKNOWN_REF")
        self.assertIn("entity:level-grond", body["detail"])

    def test_an_unprefixed_ref_is_refused(self) -> None:
        record = StateRecord.from_dict(PORTICO_RECORD_PAYLOAD)
        with self.assertRaises(StudioError) as raised:
            closure_of_refs(record, (LEVEL,))
        self.assertEqual(raised.exception.code, "UNKNOWN_REF")

    def test_neither_route_writes(self) -> None:
        before = sorted(
            path.name
            for path in self.repository.layout.runs.rglob("*")
            if path.is_file()
        )
        self.client.get("/api/state/frame")
        self.client.post(
            "/api/state/closure",
            json={
                "stateDigest": self.state_digest,
                "changedRefs": [f"entity:{LEVEL}"],
            },
        )
        after = sorted(
            path.name
            for path in self.repository.layout.runs.rglob("*")
            if path.is_file()
        )
        self.assertEqual(before, after)

    def test_the_frame_answers_for_a_record_the_kernel_will_not_view(self) -> None:
        """A record with no component tree still declares its own frame."""

        repository = make_empty_project(self.root / "unviewable")
        write_runner_record(repository, _unviewable(PORTICO_RECORD_PAYLOAD))
        client = TestClient(
            create_app(StudioSettings(project_dir=repository.layout.root))
        )
        self.addCleanup(client.close)
        state = client.get("/api/state").json()
        self.assertIsNotNone(state["componentTreeError"])
        body = client.get("/api/state/frame").json()
        self.assertEqual([row["levelId"] for row in body["levels"]], [LEVEL])


# ---- record variants, built from the fixture rather than authored beside it


def _entities(payload: dict) -> list[dict]:
    return [dict(entity) for entity in payload["entities"]]


def _without(payload: dict, schema: str) -> dict:
    return {
        **payload,
        "entities": [e for e in _entities(payload) if e["schema"] != schema],
    }


def _diagonal_axis(payload: dict) -> dict:
    entities = []
    for entity in _entities(payload):
        if entity["schema"] == "GridAxis@1":
            entity = {
                **entity,
                "fields": {**entity["fields"], "direction": [1.0, 0.0, 1.0]},
            }
        entities.append(entity)
    return {**payload, "entities": entities}


def _placed_on_axis(payload: dict) -> dict:
    """``portico-base`` placed against the grid role as well as the level."""

    entities = []
    for entity in _entities(payload):
        if entity["entity_id"] == BASE:
            fields = dict(entity["fields"])
            fields["references"] = {
                **fields["references"],
                "at": {"axis_point": {"axis": AXIS_ROLE, "along": 1.5}},
            }
            entity = {**entity, "fields": fields}
        entities.append(entity)
    return {**payload, "entities": entities}


def _unviewable(payload: dict) -> dict:
    """A record the kernel parses and refuses to build a bound view for.

    A record with no declared option: the record itself accepts that (an
    option is optional), and ``developed_design_view`` refuses it because it
    has no option id to name the design it is a view of. The entities — the
    level, the axis and the elements — are untouched, which is the point.
    """

    return {key: value for key, value in payload.items() if key != "option"}


if __name__ == "__main__":
    unittest.main()
