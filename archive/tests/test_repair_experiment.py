"""P063 repair-experiment contract tests: deterministic, provider-free."""

import unittest

from archive.archflow.evaluation.repair_experiment import (
    BaselineGraph,
    EditClass,
    EpisodeStatus,
    FrozenRepairDelta,
    GoldImpactSet,
    RepairStrategy,
    run_episode,
)


def _graph() -> BaselineGraph:
    design_program = {
        "nodes": [
            {"node_id": "function-1", "label": "hall"},
            {"node_id": "function-2", "label": "service"},
        ],
        "relationships": [
            {
                "relationship_id": "hall-service",
                "kind": "adjacency",
                "source_node_ref": "program-node:function-1",
                "target_node_ref": "program-node:function-2",
            }
        ],
    }
    proposal = {
        "components": [
            {"component_id": "building", "parent_component_id": None,
             "volume_ids": [], "revision": 0},
            {"component_id": "hall", "parent_component_id": "building",
             "volume_ids": ["volume-hall"], "revision": 0},
            {"component_id": "service", "parent_component_id": "building",
             "volume_ids": ["volume-service"], "revision": 0},
        ],
        "volumes": [
            {"volume_id": "volume-hall",
             "bounds": {"minimum": [0, 0, 0], "maximum": [9, 3, 9]}},
            {"volume_id": "volume-service",
             "bounds": {"minimum": [10, 0, 0], "maximum": [14, 3, 9]}},
        ],
        "zones": [
            {"zone_id": "zone-hall",
             "program_node_refs": ["program-node:function-1"],
             "volume_ids": ["volume-hall"]},
            {"zone_id": "zone-service",
             "program_node_refs": ["program-node:function-2"],
             "volume_ids": ["volume-service"]},
        ],
        "connections": [
            {"connection_id": "connection-1",
             "relationship_refs": ["program-relationship:hall-service"],
             "source_zone_id": "zone-hall",
             "target_zone_id": "zone-service"}
        ],
        "constraint_responses": [],
        "levels": [{"level_id": "l0", "base_y": 0, "height": 4}],
    }
    geometry = {
        "proposal": {
            "semantic_bindings": [
                {"binding_id": "binding-hall", "component_id": "hall",
                 "object_ids": ["object-hall"]}
            ],
            "operations": [
                {"op_id": "op-hall",
                 "semantic_binding_ids": ["binding-hall"],
                 "output_object_ids": ["object-hall"],
                 "parameters": [
                     {"name": "size", "value_json": "[9.0, 3.0, 9.0]"}
                 ]}
            ],
        },
        "objects": [{"object_id": "object-hall"}],
    }
    scene = {
        "objects": [{"object_id": "object-hall",
                     "bounds": {"minimum": [0, 0, 0],
                                "maximum": [9, 3, 9]}}],
        "opening_object_ids": ["object-door"],
    }
    criteria = [
        {"criterion_id": "coverage", "mandatory": True,
         "measurement_key": "program_component_coverage_ratio",
         "operator": "minimum", "expected_json": "1.0", "unit": None,
         "component_ids": ["hall", "service"], "geometry_object_ids": []},
    ]
    return BaselineGraph(
        design_program=design_program,
        proposal=proposal,
        geometry=geometry,
        scene=scene,
        criteria=criteria,
    ).build()


def _evaluate(graph: BaselineGraph):
    covered = {
        ref.rsplit(":", 1)[-1]
        for zone in graph.proposal["zones"]
        for ref in zone["program_node_refs"]
    }
    nodes = {n["node_id"] for n in graph.design_program["nodes"]}
    status = "pass" if nodes <= covered else "fail"
    return (("coverage", status),)


_DELTA = FrozenRepairDelta(
    delta_id="test-relation",
    case_id="case-test",
    edit_class=EditClass.PROGRAM_RELATION,
    target_node="program-relationship:hall-service",
    payload={"kind": "separation"},
    rationale="test",
)


class RepairExperimentContractTests(unittest.TestCase):
    def test_frozen_delta_digest_is_stable_and_schema_bound(self):
        clone = FrozenRepairDelta.from_dict(_DELTA.to_dict())
        self.assertEqual(_DELTA.delta_digest, clone.delta_digest)
        self.assertEqual("FrozenRepairDelta@1", _DELTA.to_dict()["schema"])

    def test_closure_follows_only_explicit_edges(self):
        graph = _graph()
        closure = graph.closure("program-relationship:hall-service")
        self.assertIn("connection:connection-1", closure)
        self.assertNotIn("binding:binding-hall", closure)
        self.assertNotIn("scene-object:object-hall", closure)

    def test_target_only_leaves_stale_dependencies(self):
        result = run_episode(
            episode_id="e1",
            baseline=_graph(),
            delta=_DELTA,
            strategy=RepairStrategy.TARGET_ONLY,
            evaluate=_evaluate,
            commitments=("commitment:x",),
        )
        self.assertIs(EpisodeStatus.REPAIR_FAILED, result.status)
        self.assertTrue(
            result.first_failure.startswith("stale-dependency:")
        )

    def test_dependency_scoped_matches_gold_closure_exactly(self):
        graph = _graph()
        gold = GoldImpactSet(
            delta_id=_DELTA.delta_id,
            annotator="harness:test",
            annotator_is_harness=True,
            affected_nodes=tuple(
                sorted(graph.closure(_DELTA.target_node))
            ),
            expected_first_failure=None,
            basis="typed closure",
        )
        result = run_episode(
            episode_id="e2",
            baseline=graph,
            delta=_DELTA,
            strategy=RepairStrategy.DEPENDENCY_SCOPED,
            evaluate=_evaluate,
            commitments=("commitment:x",),
        )
        metrics = result.metrics(gold)
        self.assertTrue(metrics["no_new_validator_failures"])
        self.assertEqual(1.0, metrics["impact_precision"])
        self.assertEqual(1.0, metrics["impact_recall"])
        self.assertLess(metrics["recompute_ratio"], 1.0)

    def test_whole_chain_recomputes_every_derived_node(self):
        graph = _graph()
        result = run_episode(
            episode_id="e3",
            baseline=graph,
            delta=_DELTA,
            strategy=RepairStrategy.WHOLE_CHAIN,
            evaluate=_evaluate,
            commitments=("commitment:x",),
        )
        self.assertEqual(
            result.recomputed_derived, result.eligible_nodes
        )
        self.assertEqual((), result.new_failures)

    def test_component_replacement_repairs_follow_the_delta_rename(self):
        graph = _graph()
        delta = FrozenRepairDelta(
            delta_id="test-replacement",
            case_id="case-test",
            edit_class=EditClass.COMPONENT_REPLACEMENT,
            target_node="component:hall",
            payload={"replacement_component_id": "hall-v2"},
            rationale="test",
        )
        result = run_episode(
            episode_id="e4",
            baseline=graph,
            delta=delta,
            strategy=RepairStrategy.DEPENDENCY_SCOPED,
            evaluate=_evaluate,
            commitments=(),
        )
        self.assertEqual((), result.new_failures)

    def test_inapplicable_delta_is_typed_not_silent(self):
        delta = FrozenRepairDelta(
            delta_id="test-absent",
            case_id="case-test",
            edit_class=EditClass.PROGRAM_RELATION,
            target_node="program-relationship:absent",
            payload={"kind": "separation"},
            rationale="test",
        )
        result = run_episode(
            episode_id="e5",
            baseline=_graph(),
            delta=delta,
            strategy=RepairStrategy.DEPENDENCY_SCOPED,
            evaluate=_evaluate,
            commitments=(),
        )
        self.assertIs(EpisodeStatus.DELTA_INAPPLICABLE, result.status)


if __name__ == "__main__":
    unittest.main()
