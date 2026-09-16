"""Typed Runtime refusals survive HTTP and MCP without secrets or blind retries."""
import io
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[4]
for directory in (ROOT, ROOT / "apps/archflow-studio/api", ROOT / "apps/monkeyhub/api"):
    if str(directory) not in sys.path: sys.path.insert(0, str(directory))
from monkeyhub_api import chat
from monkeyhub_api.models import HubFailure


class Stream(io.StringIO):
    def reconfigure(self, **kwargs): pass


class ModelingErrorTests(unittest.TestCase):
    def refused(self, status, body):
        opener = Mock()
        opener.open.side_effect = HTTPError("http://127.0.0.1:8791/api/proposals", status, "refused", {}, io.BytesIO(body))
        with patch.object(chat, "build_opener", return_value=opener), self.assertRaises(HubFailure) as result:
            chat._request_json("http://127.0.0.1:8791", "/api/proposals", "POST", {})
        opener.open.assert_called_once()
        return result.exception

    def test_runtime_classes_remain_distinct_and_are_not_retried(self):
        for status,code in ((422,"SEMANTIC_EDIT_INVALID"),(409,"STALE_BASE"),(409,"PROPOSAL_CHAIN_CONFLICT"),(403,"PROJECT_MISMATCH")):
            with self.subTest(code=code):
                error = self.refused(status,json.dumps({"code":code,"detail":"fixture refusal"}).encode())
                self.assertEqual((error.status,error.error.code,error.error.detail),(status,code,"fixture refusal"))

    def test_untyped_or_malformed_responses_have_a_bounded_redacted_fallback(self):
        for body in (b"not JSON", b"[]", b'{"code":false,"detail":"refused"}', b'{"code":"not a code","detail":"refused"}'):
            with self.subTest(body=body):
                self.assertEqual(self.refused(500,body).error.code,"CHAT_TOOL_FAILED")
        secret="fixture-super-secret-token"
        with patch.dict(os.environ, {"FIXTURE_API_TOKEN":secret}):
            error=self.refused(422,json.dumps({"code":"SEMANTIC_EDIT_INVALID","detail":secret+"x"*5000}).encode())
        self.assertNotIn(secret,error.error.detail)
        self.assertLessEqual(len(error.error.detail),1200)

    def test_mcp_preserves_class_and_status_while_remaining_an_error(self):
        incoming=Stream(json.dumps({"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"studio_request","arguments":{"method":"POST","path":"/api/proposals"}}})+"\n")
        outgoing=Stream()
        with patch.object(chat.sys,"stdin",incoming),patch.object(chat.sys,"stdout",outgoing),patch.object(chat,"call_tool",side_effect=HubFailure(409,"STALE_BASE","Re-read the exact source")) as call:
            chat._mcp("http://127.0.0.1:8790","fixture-chat")
        result=json.loads(outgoing.getvalue())["result"]
        self.assertTrue(result["isError"])
        self.assertEqual(json.loads(result["content"][0]["text"]),{"code":"STALE_BASE","detail":"Re-read the exact source","httpStatus":409})
        call.assert_called_once()
