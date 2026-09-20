"""Accepted boundaries replace native context once; candidates keep continuity."""

from copy import deepcopy
import unittest
from unittest.mock import patch

import test_chat as cli_fixture
import test_acp_chat as acp_fixture
from monkeyhub_api import acp_session, chat
from monkeyhub_api.models import ChatDesignContext, ChatPostRequest, HubFailure


def stage_pack(stage_ref="stage-massing", *, accepted=True):
    pack = deepcopy(cli_fixture.ChatTests.PACK)
    pack["confirmedStage"] = {
        "stageRef": stage_ref, "label": "Confirmed massing", "runId": "run-001",
        "stateDigest": "a" * 64, "isSource": accepted,
        "lockedParameterKeys": ["mass.height"], "retainedConditionRefs": ["entity:entrance"],
    }
    return pack


class StageHandoffTests(unittest.TestCase):
    def setUp(self):
        self.fixture = cli_fixture.ChatTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def established(self, provider="codex"):
        """A chat with a provider session that a boundary would have to replace."""
        session = self.fixture.create(provider=provider)
        self.fixture.post(session, "OLD_TRANSCRIPT_ONLY")
        self.fixture.finished(session)
        saved = self.fixture.store._sessions[session.id]
        self.assertTrue(saved.nativeSessionId)
        return session, saved.nativeSessionId, saved.cliStartId

    def boundary_request(self, session, content="Build the walls from the saved stage"):
        self.fixture.PACK = stage_pack()
        return ChatPostRequest(projectId=session.projectId, content=content,
                               contextMode="stage", designContext=self.fixture.selected())

    def assert_restored(self, session, result, old_id, old_start):
        """The rotation is undone; the turn that attempted it is not."""
        saved = self.fixture.store._sessions[session.id]
        self.assertEqual((saved.nativeSessionId, saved.cliStartId), (old_id, old_start))
        self.assertEqual(saved.priorProviderSessionIds, [])
        self.assertIsNone(saved.providerStageRef)
        boundary = [m for m in result.messages if m.role == "user"][-1]
        self.assertEqual((boundary.contextMode, boundary.confirmedStageRef, boundary.confirmedStageLabel),
                         ("continue", None, None), "nothing was handed off, so no boundary is shown")
        self.assertEqual([m.content for m in result.messages if m.role == "user"],
                         ["OLD_TRANSCRIPT_ONLY", "Build the walls from the saved stage"])
        # The restored identity is the one the next ordinary turn continues.
        self.fixture.post(session, "Keep talking")
        self.assertEqual(self.fixture.finished(session).status, "idle")
        self.assertIn(old_id, self.fixture.calls()[-1]["args"])

    def turn(self, session, pack, *, mode="stage"):
        fixture = self.fixture
        fixture.PACK = pack
        with patch.object(chat, "_request_json", side_effect=fixture.studio(session, [])):
            fixture.store.post(session.id, ChatPostRequest(
                projectId=session.projectId, content="Build the walls from the saved stage",
                contextMode=mode, designContext=fixture.selected()))
            result = fixture.finished(session)
        self.assertEqual(result.status, "idle", result.error)
        return result

    def test_confirmed_boundary_rotates_once_across_candidates_and_restart(self):
        fixture = self.fixture
        for provider in ("codex", "claude"):
            with self.subTest(provider=provider):
                session = fixture.create(provider=provider)
                fixture.post(session, "OLD_TRANSCRIPT_ONLY")
                fixture.finished(session)
                old_id = fixture.store._sessions[session.id].nativeSessionId
                result = self.turn(session, stage_pack())
                new_id = fixture.store._sessions[session.id].nativeSessionId
                self.assertNotEqual(new_id, old_id)
                self.assertNotIn(old_id, fixture.calls()[-1]["args"])
                self.assertNotIn("OLD_TRANSCRIPT_ONLY", fixture.calls()[-1]["prompt"])
                self.assertIn("mass.height", fixture.calls()[-1]["prompt"])
                self.assertIn("entity:entrance", fixture.calls()[-1]["prompt"])
                boundary = [m for m in result.messages if m.role == "user"][-1]
                self.assertEqual((boundary.contextMode, boundary.confirmedStageRef), ("stage", "stage-massing"))
                self.assertEqual(boundary.confirmedStageLabel, "Confirmed massing")
                # A repeated accepted source and an unaccepted candidate from
                # even another Stage are not new confirmation boundaries.
                self.turn(session, stage_pack())
                self.turn(session, stage_pack("other-stage", accepted=False))
                self.assertEqual(fixture.store._sessions[session.id].nativeSessionId, new_id)
                self.assertEqual(fixture.store._sessions[session.id].priorProviderSessionIds, [old_id])
                fixture.store.shutdown()
                fixture.store = chat.ChatStore(fixture.runtime, fixture.store.hub_url, commands=fixture.commands)
                self.turn(session, stage_pack())
                self.assertIn(new_id, fixture.calls()[-1]["args"])
                self.assertEqual(fixture.store._sessions[session.id].providerStageRef, "stage-massing")
                self.turn(session, stage_pack("stage-walls"))
                self.assertNotEqual(fixture.store._sessions[session.id].nativeSessionId, new_id)
                self.assertEqual(fixture.store._sessions[session.id].priorProviderSessionIds, [old_id, new_id])
                self.assertEqual([m.content for m in fixture.store.get(session.id).messages if m.role == "user"][0],
                                 "OLD_TRANSCRIPT_ONLY", "visible history is retained")

    def test_manual_reset_between_same_stage_turns_does_not_rotate_the_provider_again(self):
        fixture = self.fixture
        session = fixture.create()
        fixture.post(session, "OLD_TRANSCRIPT_ONLY")
        fixture.finished(session)
        self.turn(session, stage_pack())
        confirmed = fixture.store._sessions[session.id].nativeSessionId
        # An explicit reset from a candidate of the same Stage replaces the
        # provider without retiring the boundary that was already handed off.
        self.turn(session, stage_pack(accepted=False), mode="project")
        manual = fixture.store._sessions[session.id].nativeSessionId
        self.assertNotEqual(manual, confirmed)
        self.assertEqual(fixture.store._sessions[session.id].providerStageRef, "stage-massing")
        history = list(fixture.store._sessions[session.id].priorProviderSessionIds)
        result = self.turn(session, stage_pack())
        self.assertEqual(fixture.store._sessions[session.id].nativeSessionId, manual,
                         "returning to the same accepted Stage is not a second boundary")
        self.assertEqual(fixture.store._sessions[session.id].priorProviderSessionIds, history)
        self.assertIn(manual, fixture.calls()[-1]["args"])
        self.assertEqual([(m.contextMode, m.confirmedStageRef) for m in result.messages if m.role == "user"],
                         [("continue", None), ("stage", "stage-massing"), ("project", None), ("continue", None)])

    def test_stage_mode_requires_a_verified_source_before_posting(self):
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            ChatPostRequest(projectId="chat-project", content="Build the walls", contextMode="stage")

    def test_continue_override_and_unaccepted_source_keep_original_provider(self):
        fixture = self.fixture
        session = fixture.create()
        fixture.post(session)
        fixture.finished(session)
        identifier = fixture.store._sessions[session.id].nativeSessionId
        self.turn(session, stage_pack(), mode="continue")
        self.turn(session, stage_pack(accepted=False))
        self.turn(session, {**stage_pack(), "confirmedStage": None})
        self.assertEqual(fixture.store._sessions[session.id].nativeSessionId, identifier)
        self.assertIsNone(fixture.store._sessions[session.id].providerStageRef)
        self.assertTrue(all(m.contextMode == "continue" for m in fixture.store.get(session.id).messages))

    def test_refused_or_cancelled_boundary_preserves_provider_and_stage_marker(self):
        fixture = self.fixture
        session = fixture.create()
        self.turn(session, stage_pack())
        identifier = fixture.store._sessions[session.id].nativeSessionId
        count = len(fixture.calls())
        fixture.PACK = stage_pack("stage-walls")
        request = ChatPostRequest(projectId=session.projectId, content="Continue walls",
                                  contextMode="stage", designContext=fixture.selected())
        with patch.object(chat, "_request_json", side_effect=fixture.studio(
                session, [], HubFailure(409, "STALE_BASE", "The selected source changed"))):
            fixture.store.post(session.id, request)
            self.assertEqual(fixture.finished(session).error.code, "STALE_BASE")
        def cancelled(*args, **kwargs):
            fixture.store._running[session.id].stop.set()
            return fixture.PACK
        with patch.object(chat, "_prepared_context", side_effect=cancelled):
            fixture.store.post(session.id, request)
            self.assertEqual(fixture.finished(session).status, "interrupted")
        self.assertEqual(len(fixture.calls()), count)
        self.assertEqual(fixture.store._sessions[session.id].nativeSessionId, identifier)
        self.assertEqual(fixture.store._sessions[session.id].providerStageRef, "stage-massing")

    def test_boundary_whose_replacement_never_starts_restores_the_previous_provider(self):
        fixture = self.fixture
        session, old_id, old_start = self.established(provider="claude")
        count = len(fixture.calls())
        with patch.object(chat, "_request_json", side_effect=fixture.studio(session, [])), \
             patch.object(chat.ChatStore, "_command", side_effect=OSError("the installed CLI is missing")):
            fixture.store.post(session.id, self.boundary_request(session))
            result = fixture.finished(session)
        self.assertEqual((result.status, result.error.code), ("failed", "CHAT_PROCESS_FAILED"))
        self.assertEqual(len(fixture.calls()), count, "no replacement provider ran")
        self.assert_restored(session, result, old_id, old_start)

    def test_stop_between_the_reset_and_the_provider_restores_the_previous_provider(self):
        fixture = self.fixture
        session, old_id, old_start = self.established()
        count = len(fixture.calls())
        rotate = chat.ChatStore._fresh_provider_session

        def stopped(store, session_id, **kwargs):
            replaced = rotate(store, session_id, **kwargs)
            store._running[session_id].stop.set()
            return replaced

        with patch.object(chat, "_request_json", side_effect=fixture.studio(session, [])), \
             patch.object(chat.ChatStore, "_fresh_provider_session", autospec=True, side_effect=stopped):
            fixture.store.post(session.id, self.boundary_request(session))
            result = fixture.finished(session)
        self.assertEqual((result.status, result.error.code), ("interrupted", "CHAT_STOPPED"))
        self.assertEqual(len(fixture.calls()), count, "no replacement process was started")
        self.assert_restored(session, result, old_id, old_start)

    def test_a_replacement_that_opened_its_own_session_survives_a_failed_turn(self):
        fixture = self.fixture
        session, old_id, _ = self.established()
        with patch.object(chat, "_request_json", side_effect=fixture.studio(session, [])):
            # The fake CLI reports its new session and only then fails the turn.
            fixture.store.post(session.id, self.boundary_request(session, "model-refused-test: build the walls"))
            result = fixture.finished(session)
        self.assertEqual((result.status, result.error.code), ("failed", "CHAT_PROVIDER_FAILED"))
        saved = fixture.store._sessions[session.id]
        self.assertNotIn(saved.nativeSessionId, (None, old_id))
        self.assertEqual(saved.priorProviderSessionIds, [old_id])
        self.assertEqual(saved.providerStageRef, "stage-massing")
        boundary = [m for m in result.messages if m.role == "user"][-1]
        self.assertEqual((boundary.contextMode, boundary.confirmedStageRef), ("stage", "stage-massing"))
        fixture.post(session, "Keep talking")
        self.assertEqual(fixture.finished(session).status, "idle")
        self.assertIn(saved.nativeSessionId, fixture.calls()[-1]["args"])


class AcpStageHandoffTests(unittest.TestCase):
    def test_boundary_creates_new_acp_session_and_restart_loads_only_that_session(self):
        fixture = acp_fixture.AcpChatTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        session = fixture.create()
        fixture.post(session, "OLD_ACP_TRANSCRIPT_ONLY")
        fixture.finished(session)
        original = fixture.store._sessions[session.id].acpSessionId
        old_client = fixture.store._acp_sessions[session.id]
        request = ChatPostRequest(projectId=session.projectId, content="Build walls",
                                  contextMode="stage", designContext=ChatDesignContext(
                                      sourceRunId="run-001", stateDigest="a" * 64))
        with patch.object(chat, "_prepared_context", return_value=stage_pack()):
            fixture.store.post(session.id, request)
            fixture.finished(session)
            identifier = fixture.store._sessions[session.id].acpSessionId
            # The fixture agent hands out one session id, so a replaced session
            # shows as a second client that was created rather than loaded.
            self.assertIsNot(fixture.store._acp_sessions[session.id], old_client)
            self.assertFalse(any(c["event"] == "load" for c in fixture.calls()))
            prompt = [c for c in fixture.calls() if c["event"] == "hub_prompt"][-1]["blocks"][0]["text"]
            self.assertNotIn("OLD_ACP_TRANSCRIPT_ONLY", prompt)
            self.assertIn("entity:entrance", prompt)
            fixture.store.shutdown()
            fixture.store = fixture.open_store()
            fixture.store.post(session.id, request)
            fixture.finished(session)
        self.assertEqual(len([c for c in fixture.calls() if c["event"] == "new"]), 2)
        self.assertEqual(fixture.store._sessions[session.id].acpSessionId, identifier)
        self.assertEqual(fixture.store._sessions[session.id].priorProviderSessionIds, [original])

    def test_negotiation_that_never_reaches_a_session_keeps_the_previous_acp_session(self):
        fixture = acp_fixture.AcpChatTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        session = fixture.create()
        fixture.post(session, "first")
        fixture.finished(session)
        original = fixture.store._sessions[session.id].acpSessionId
        self.assertTrue(original)
        request = ChatPostRequest(projectId=session.projectId, content="Build walls",
                                  contextMode="stage", designContext=ChatDesignContext(
                                      sourceRunId="run-001", stateDigest="a" * 64))
        with patch.object(chat, "_prepared_context", return_value=stage_pack()), \
             patch.object(acp_session.CodexAcpSession, "prompt", side_effect=acp_session.AcpSessionError(
                 "the adapter could not open a session")):
            fixture.store.post(session.id, request)
            result = cli_fixture.wait_for(lambda: fixture.store.get(session.id), lambda row: row.status != "running")
        self.assertEqual((result.status, result.error.code), ("failed", "CHAT_ACP_FAILED"))
        saved = fixture.store._sessions[session.id]
        self.assertEqual((saved.acpSessionId, saved.priorProviderSessionIds, saved.providerStageRef),
                         (original, [], None))
        self.assertNotIn(session.id, fixture.store._acp_sessions)
        boundary = [m for m in result.messages if m.role == "user"][-1]
        self.assertEqual((boundary.contextMode, boundary.confirmedStageRef), ("continue", None))
        # The retained identity is opened again on the next turn's own adapter.
        fixture.post(session, "second")
        fixture.finished(session)
        self.assertEqual([c["event"] for c in fixture.calls() if c["event"] in {"new", "load"}], ["new", "load"])
        self.assertEqual(fixture.store._sessions[session.id].acpSessionId, original)


if __name__ == "__main__":
    unittest.main()
