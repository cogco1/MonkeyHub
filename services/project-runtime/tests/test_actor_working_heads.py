"""Authenticated collaborators keep independent editing bases and recovery drafts (GH-619)."""

from concurrent.futures import ThreadPoolExecutor
import json
from threading import Barrier
from pathlib import Path

from fastapi.testclient import TestClient

from project_runtime.main import create_app
from project_runtime.settings import StudioSettings

from .support import PROJECT_ID, REFERENCE_RUN_ID
from .test_candidate import CandidateTestCase
from .test_working_copies import register_model


class ActorWorkingHeadTests(CandidateTestCase):
    def setUp(self):
        super().setUp()
        self.addCleanup(self.app.state.jobs.shutdown)
        actors_file = self.root / "actors.json"
        actors_file.write_text(json.dumps({"actors": [
            {"actor_id": actor, "display_name": actor.title(), "token": f"{actor}-test-secret",
             "projects": {PROJECT_ID: actions}}
            for actor, actions in (
                ("alice", ["read", "propose"]), ("bob", ["read", "propose"]),
                ("viewer", ["read"]), ("reviewer", ["read", "accept"]),
            )
        ]}), encoding="utf-8")
        self.settings = StudioSettings(
            project_dir=self.repository.layout.root, cad_export="off", mode="remote",
            origins=("https://studio.example",), actors_file=actors_file,
            project_owner_actor_id="alice",
        )
        self.app = create_app(self.settings)
        self.addCleanup(self.app.state.jobs.shutdown)
        self.client = TestClient(self.app, headers=self.headers("alice"))
        self.addCleanup(self.client.close)

    @staticmethod
    def headers(actor):
        return {"Authorization": f"Bearer {actor}-test-secret"}

    def read(self, actor, client=None):
        response = (client or self.client).get("/api/working-draft", headers=self.headers(actor))
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def select(self, actor, revision, run_id, **extra):
        return self.client.put("/api/working-draft", headers=self.headers(actor), json={
            "projectId": PROJECT_ID, "baseRevisionSha256": revision, "runId": run_id, **extra,
        })

    def candidates(self):
        runs = []
        for height in (2.2, 2.4):
            accepted, job = self.run_candidate(f"set height to {height}", elementId="portico-base")
            self.assertEqual(job["status"], "succeeded", job)
            runs.append(accepted["candidateId"])
        return runs

    def split_heads(self):
        # Both windows read before either Continue. A colleague's move must not
        # invalidate the other window's personal compare-and-swap revision.
        first, second = self.candidates()
        alice, bob = self.read("alice"), self.read("bob")
        for actor, position, run_id in (("alice", alice, first), ("bob", bob, second)):
            response = self.select(actor, position["revisionSha256"], run_id)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["current"]["runId"], run_id)
        return first, second

    def test_distinct_heads_survive_stale_own_window_and_runtime_restart(self):
        head_before = self.repository.read_head()
        first, second = self.split_heads()
        alice, bob = self.read("alice"), self.read("bob")
        moved = self.select("alice", alice["revisionSha256"], second)
        self.assertEqual(moved.status_code, 200, moved.text)
        stale = self.select("alice", alice["revisionSha256"], first)
        self.assertEqual((stale.status_code, stale.json()["code"]), (409, "WORKING_DRAFT_STALE"))
        self.assertEqual(self.read("bob"), bob)
        returned = self.select("alice", moved.json()["revisionSha256"], first)
        self.assertEqual(returned.status_code, 200, returned.text)
        with TestClient(create_app(self.settings)) as cold:
            self.assertEqual(self.read("alice", cold)["current"]["runId"], first)
            self.assertEqual(self.read("bob", cold)["current"]["runId"], second)
        self.assertEqual(self.repository.read_head(), head_before)
        self.assertEqual(self.repository.read_design_branches(), {})

    def test_body_identity_cannot_move_someone_elses_head(self):
        first, second = self.split_heads()
        before = {actor: self.read(actor) for actor in ("alice", "bob")}
        for field in ("actorId", "actor_id"):
            spoofed = self.select("bob", before["bob"]["revisionSha256"], first, **{field: "alice"})
            self.assertEqual(spoofed.status_code, 422, spoofed.text)
        self.assertEqual(self.read("alice"), before["alice"])
        self.assertEqual(self.read("bob"), before["bob"])
        self.assertEqual(self.read("bob")["current"]["runId"], second)

    def test_local_recovery_is_private_and_colleague_save_does_not_make_it_stale(self):
        source = {"projectId": PROJECT_ID, "sourceRunId": REFERENCE_RUN_ID,
                  "sourceStageRef": None, "stateDigest": self.state_digest}
        initial = {actor: self.read(actor) for actor in ("alice", "bob")}
        drafts = {}
        for actor, offset in (("alice", 1), ("bob", 2)):
            draft = {"source": source, "commands": [{"kind": "translate", "offset": [offset, 0, 0]}],
                     "attempt": {"requestId": f"{actor}-pending"}}
            response = self.client.put("/api/working-draft/local", headers=self.headers(actor), json={
                "projectId": PROJECT_ID, "baseRevisionSha256": initial[actor]["revisionSha256"], "draft": draft,
            })
            self.assertEqual(response.status_code, 200, response.text)
            drafts[actor] = response.json()
        for actor in drafts:
            self.assertEqual(self.read(actor)["localDraft"], drafts[actor]["localDraft"])
        self.assertIsNone(self.read("viewer")["localDraft"])
        stale = self.client.put("/api/working-draft/local", headers=self.headers("alice"), json={
            "projectId": PROJECT_ID, "baseRevisionSha256": initial["alice"]["revisionSha256"], "draft": None,
        })
        self.assertEqual((stale.status_code, stale.json()["code"]), (409, "WORKING_DRAFT_STALE"))

    def test_legacy_position_belongs_to_owner_and_reads_do_not_migrate_bytes(self):
        # Retain the old format through the repository's existing compatibility API.
        value, revision = self.repository.read_working_draft()
        value["runs"][REFERENCE_RUN_ID] = {
            "sourceStageRef": None, "branchId": None, "label": "Legacy study",
            "updatedAt": "2026-01-01T00:00:00+00:00", "automatic": False,
        }
        value["current"] = REFERENCE_RUN_ID
        self.repository.compare_and_swap_working_draft(expected_revision=revision, value=value)
        path = self.repository.layout.working_draft
        before = path.read_bytes()
        self.assertEqual(json.loads(before)["schema"], "ProjectWorkingDraft@1")
        self.assertEqual(self.read("alice")["current"]["runId"], REFERENCE_RUN_ID)
        self.assertIsNone(self.read("bob")["current"])
        self.assertIsNone(self.read("bob")["revisionSha256"])
        self.assertEqual(path.read_bytes(), before)

    def test_working_source_and_graph_cache_cannot_reuse_another_actors_head(self):
        first, second = self.split_heads()
        for route in ("/api/working-source?workspace=modeling", "/api/worktrees"):
            with self.subTest(route=route):
                alice = self.client.get(route, headers=self.headers("alice"))
                self.assertEqual(alice.status_code, 200, alice.text)
                self.assertEqual(alice.json()["head"]["runId"], first)
                tag = alice.headers["etag"]
                bob = self.client.get(route, headers={**self.headers("bob"), "If-None-Match": tag})
                self.assertEqual(bob.status_code, 200, bob.text)
                self.assertEqual(bob.json()["head"]["runId"], second)
                self.assertNotEqual(bob.headers["etag"], tag)
                if route == "/api/worktrees":
                    for graph in (alice.json(), bob.json()):
                        self.assertEqual(
                            [(row["actorId"], row["displayName"], row["runId"]) for row in graph["actorHeads"]],
                            [("alice", "Alice", first), ("bob", "Bob", second)],
                        )
                again = self.client.get(route, headers=self.headers("alice"))
                self.assertEqual(again.json()["head"]["runId"], first)

    def test_simultaneous_actor_requests_do_not_leak_request_context(self):
        first, second = self.split_heads()
        barrier = Barrier(2)

        def read_actor(actor, expected):
            with TestClient(self.app, headers=self.headers(actor)) as client:
                for _ in range(4):
                    barrier.wait(timeout=10)
                    for route in ("/api/working-source?workspace=modeling", "/api/worktrees"):
                        response = client.get(route)
                        self.assertEqual(response.status_code, 200, response.text)
                        self.assertEqual(response.json()["head"]["runId"], expected)
                    self.assertEqual(self.read(actor, client)["current"]["runId"], expected)

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(read_actor, actor, run_id)
                       for actor, run_id in (("alice", first), ("bob", second))]
            for future in futures:
                future.result(timeout=30)

    def test_viewer_cannot_continue_and_accept_still_requires_accept_grant(self):
        model_bytes = (Path(__file__).parent / "fixtures/model-source-a.3dm").read_bytes()
        model = register_model(self.client, REFERENCE_RUN_ID, self.state_digest, model_bytes)["modelSource"]
        initialized = self.client.post("/api/design-stages/initialize", headers=self.headers("reviewer"),
                                       json={"projectId": PROJECT_ID, "modelSource": model})
        self.assertEqual(initialized.status_code, 201, initialized.text)
        stage = initialized.json()
        accepted, job = self.run_candidate("set height to 2.2", elementId="portico-base",
                                           sourceRunId=stage["candidateId"], sourceStageRef=stage["stageRef"])
        self.assertEqual(job["status"], "succeeded", job)
        candidate = accepted["candidateId"]
        state = self.client.get("/api/state", params={"run": candidate}).json()
        register_model(self.client, candidate, state["stateDigest"], model_bytes)
        # A new collaborator follows the verified shared Stage, even when a
        # newer unaccepted candidate exists and has a complete registered model.
        self.assertIsNone(self.read("bob")["current"])
        for route in ("/api/working-source?workspace=modeling", "/api/worktrees"):
            default = self.client.get(route, headers=self.headers("bob"))
            self.assertEqual(default.status_code, 200, default.text)
            self.assertEqual(default.json()["head"]["runId"], stage["candidateId"])
        denied = self.select("viewer", self.read("viewer")["revisionSha256"], candidate)
        self.assertEqual(denied.status_code, 403, denied.text)
        body = {"projectId": PROJECT_ID, "branchId": "main", "expectedHeadStageRef": stage["stageRef"]}
        for actor in ("viewer", "alice", "bob"):
            denied = self.client.post(f"/api/candidates/{candidate}/accept", headers=self.headers(actor), json=body)
            self.assertEqual(denied.status_code, 403, denied.text)
        accepted = self.client.post(f"/api/candidates/{candidate}/accept", headers=self.headers("reviewer"), json=body)
        self.assertEqual(accepted.status_code, 200, accepted.text)
        self.assertEqual(accepted.json()["candidateId"], candidate)

    def test_migrated_project_reopened_locally_continues_owner_without_moving_colleague(self):
        first, second = self.split_heads()
        self.assertEqual(self.repository.read_working_draft()[0]["ownerActorId"], "alice")
        bob_before = self.read("bob")
        # The ordinary local runtime has neither actor configuration nor an
        # explicit owner override. Persisted ownership must survive this reopen.
        with TestClient(create_app(StudioSettings(
            project_dir=self.repository.layout.root, cad_export="off",
        ))) as local:
            current = local.get("/api/working-draft")
            self.assertEqual(current.status_code, 200, current.text)
            self.assertEqual(current.json()["current"]["runId"], first)
            moved = local.put("/api/working-draft", json={
                "projectId": PROJECT_ID, "baseRevisionSha256": current.json()["revisionSha256"],
                "runId": REFERENCE_RUN_ID,
            })
            self.assertEqual(moved.status_code, 200, moved.text)
            self.assertEqual(moved.json()["current"]["runId"], REFERENCE_RUN_ID)
            source = local.get("/api/working-source?workspace=modeling")
            self.assertEqual(source.status_code, 200, source.text)
            self.assertEqual(source.json()["head"]["runId"], REFERENCE_RUN_ID)
        self.assertEqual(self.read("alice")["current"]["runId"], REFERENCE_RUN_ID)
        self.assertEqual(self.read("bob"), bob_before)
        self.assertEqual(self.read("bob")["current"]["runId"], second)

    def test_same_accepted_run_keeps_each_actors_selected_branch(self):
        model_bytes = (Path(__file__).parent / "fixtures/model-source-a.3dm").read_bytes()
        model = register_model(self.client, REFERENCE_RUN_ID, self.state_digest, model_bytes)["modelSource"]
        initialized = self.client.post("/api/design-stages/initialize", headers=self.headers("reviewer"),
                                       json={"projectId": PROJECT_ID, "modelSource": model})
        self.assertEqual(initialized.status_code, 201, initialized.text)
        stage = initialized.json()
        forked = self.client.post("/api/design-branches", headers=self.headers("reviewer"), json={
            "projectId": PROJECT_ID, "branchId": "alternative", "parentBranch": "main",
            "stageRef": stage["stageRef"],
        })
        self.assertEqual(forked.status_code, 201, forked.text)
        for actor, branch in (("alice", "main"), ("bob", "alternative")):
            selected = self.select(actor, self.read(actor)["revisionSha256"], stage["candidateId"], branchId=branch)
            self.assertEqual(selected.status_code, 200, selected.text)
            self.assertEqual(selected.json()["current"]["branchId"], branch)
        for actor, branch in (("alice", "main"), ("bob", "alternative")):
            self.assertEqual(self.read(actor)["current"]["branchId"], branch)
            for route in ("/api/working-source?workspace=modeling", "/api/worktrees"):
                response = self.client.get(route, headers=self.headers(actor))
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["head"]["runId"], stage["candidateId"])
                self.assertEqual(response.json()["head"]["branchId"], branch)

    def test_peer_continue_changes_index_projection_but_private_recovery_does_not(self):
        from project_runtime.binding import bound_project
        from project_runtime.index import StudioProjector

        first, second = self.split_heads()
        binding = bound_project(self.app.state)
        projector = StudioProjector(binding)
        before = projector.project_working()
        self.assertEqual(before["actorHeads"]["bob"]["current"], second)
        moved = self.select("bob", self.read("bob")["revisionSha256"], first)
        self.assertEqual(moved.status_code, 200, moved.text)
        after = projector.project_working()
        # Both runs were already registered. Only Bob's personal editing base
        # changed, so its projection must independently invalidate peer views.
        self.assertEqual(after["current"], before["current"])
        self.assertEqual(after["runsDigest"], before["runsDigest"])
        self.assertEqual(after["active"], before["active"])
        self.assertNotEqual(after, before)
        self.assertEqual(after["actorHeads"]["bob"], {"current": first, "branchId": None})
        state = self.client.get("/api/state", params={"run": first}).json()
        draft = {"source": {"projectId": PROJECT_ID, "sourceRunId": first,
                            "sourceStageRef": None, "stateDigest": state["stateDigest"]},
                 "commands": [{"kind": "translate", "offset": [1, 0, 0]}], "attempt": None}
        recovered = self.client.put("/api/working-draft/local", headers=self.headers("bob"), json={
            "projectId": PROJECT_ID, "baseRevisionSha256": moved.json()["revisionSha256"], "draft": draft,
        })
        self.assertEqual(recovered.status_code, 200, recovered.text)
        self.assertIsNotNone(self.read("bob")["localDraft"])
        self.assertEqual(projector.project_working(), after)
        # Return to default writes no Continue audit or new run row. The
        # positions-only change must still make peers refresh their graph.
        cleared = self.select("bob", recovered.json()["revisionSha256"], None)
        self.assertEqual(cleared.status_code, 200, cleared.text)
        cleared_projection = projector.project_working()
        self.assertNotEqual(cleared_projection, after)
        self.assertEqual(cleared_projection["actorHeads"]["bob"], {"current": None, "branchId": None})
        for field in ("current", "active", "runsDigest"):
            self.assertEqual(cleared_projection[field], after[field])

    def test_return_to_parent_follows_each_actors_last_continued_fork(self):
        older, newer = self.candidates()
        # Alice's final fork choice is the older candidate, opposite creation
        # order. Bob then visits both in the opposite order, without changing
        # which line Alice left when she returns to the common parent.
        for actor, path in (("alice", (newer, older, REFERENCE_RUN_ID)),
                            ("bob", (older, newer, REFERENCE_RUN_ID))):
            for run_id in path:
                moved = self.select(actor, self.read(actor)["revisionSha256"], run_id)
                self.assertEqual(moved.status_code, 200, moved.text)
        for actor, expected in (("alice", older), ("bob", newer)):
            graph = self.client.get("/api/worktrees", headers=self.headers(actor))
            self.assertEqual(graph.status_code, 200, graph.text)
            self.assertEqual(graph.json()["head"]["runId"], REFERENCE_RUN_ID)
            self.assertEqual([step["runId"] for step in graph.json()["later"]], [expected])

    def test_owner_keeps_legacy_local_continue_history_when_actors_are_enabled(self):
        child, _ = self.candidates()
        # The original unauthenticated runtime wrote local-boundary Continue
        # events before this project ever acquired personal positions.
        with TestClient(create_app(StudioSettings(
            project_dir=self.repository.layout.root, cad_export="off",
        ))) as local:
            for run_id in (child, REFERENCE_RUN_ID):
                current = local.get("/api/working-draft").json()
                moved = local.put("/api/working-draft", json={
                    "projectId": PROJECT_ID, "baseRevisionSha256": current["revisionSha256"], "runId": run_id,
                })
                self.assertEqual(moved.status_code, 200, moved.text)
            self.assertEqual([step["runId"] for step in local.get("/api/worktrees").json()["later"]], [child])
        self.assertEqual(self.repository.read_working_draft()[0]["schema"], "ProjectWorkingDraft@1")

        def check_owner_history(client):
            for actor, expected in (("alice", [child]), ("bob", [])):
                graph = client.get("/api/worktrees", headers=self.headers(actor))
                self.assertEqual(graph.status_code, 200, graph.text)
                self.assertEqual([step["runId"] for step in graph.json()["later"]], expected)

        check_owner_history(self.client)
        migrated = self.select("alice", self.read("alice")["revisionSha256"], REFERENCE_RUN_ID)
        self.assertEqual(migrated.status_code, 200, migrated.text)
        self.assertEqual(self.repository.read_working_draft()[0]["schema"], "ProjectWorkingDraft@2")
        # Give Bob the same parent so his empty later path proves attribution
        # isolation rather than merely the absence of a selected head.
        bob = self.select("bob", self.read("bob")["revisionSha256"], REFERENCE_RUN_ID)
        self.assertEqual(bob.status_code, 200, bob.text)
        with TestClient(create_app(self.settings)) as reopened:
            check_owner_history(reopened)

    def test_legacy_local_worktree_wire_omits_team_metadata(self):
        with TestClient(create_app(StudioSettings(project_dir=self.repository.layout.root, cad_export="off"))) as local:
            body = local.get("/api/worktrees")
            self.assertEqual(body.status_code, 200, body.text)
            self.assertNotIn("actorHeads", body.json())
        self.split_heads()
        with TestClient(create_app(StudioSettings(project_dir=self.repository.layout.root, cad_export="off"))) as local:
            body = local.get("/api/worktrees")
            self.assertEqual(body.status_code, 200, body.text)
            self.assertEqual({head["actorId"] for head in body.json()["actorHeads"]}, {"alice", "bob"})
