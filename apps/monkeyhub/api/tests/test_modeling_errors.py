"""Typed Runtime refusals survive HTTP and MCP without secrets or blind retries."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import ssl
import sys
import threading
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.dev import source_roots  # noqa: E402 - this checkout's tools, found above

# The checkout's Python source roots, as its architecture policy lists them, go first.
source_roots.put_first(ROOT)
from monkeyhub_api import chat
from monkeyhub_api.models import HubFailure


class Stream(io.StringIO):
    def reconfigure(self, **kwargs): pass


class ModelingErrorTests(unittest.TestCase):
    def refused(self, status, body):
        opener = Mock()
        opener.open.side_effect = HTTPError("http://127.0.0.1:8791/api/proposals", status, "refused", {}, io.BytesIO(body))
        with patch.object(chat, "_SERVICE_OPENER", opener), self.assertRaises(HubFailure) as result:
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

    def test_geometry_rows_in_a_semantic_edit_are_refused_before_any_request(self):
        """#419: the Hub names the construction route across MCP; no Hub, runtime or Studio is asked."""
        body={"stateDigest":"a"*64,"semanticEdit":{"summary":"a block","entities":[
            {"entity_id":"block-1-body","schema":"Element@1","fields":{"component_id":"block-1","producer":"prism"}}]}}
        incoming=Stream(json.dumps({"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"studio_request",
            "arguments":{"method":"POST","path":"/api/proposals","body":body}}})+"\n")
        outgoing=Stream()
        with patch.object(chat.sys,"stdin",incoming),patch.object(chat.sys,"stdout",outgoing), \
                patch.object(chat,"_request_json") as request,patch.object(chat,"_bound_studio") as studio:
            chat._mcp("http://127.0.0.1:8790","fixture-chat")
        result=json.loads(outgoing.getvalue())["result"]
        self.assertTrue(result["isError"])
        failure=json.loads(result["content"][0]["text"])
        self.assertEqual((failure["code"],failure["httpStatus"]),("CHAT_TOOL_INVALID",422))
        self.assertTrue(failure["detail"].startswith("Geometry is authored with POST /api/proposals/construction;"))
        request.assert_not_called()
        studio.assert_not_called()


@contextmanager
def _serving():
    """A bound-service stand-in on a loopback port: GET answers JSON, /moved redirects."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/moved":
                self.send_response(302)
                self.send_header("Location", "/api/protocol")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            body = b'{"ok": true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


class ServiceCallTests(unittest.TestCase):
    """What one call from a chat to its bound service costs the Hub (#363)."""

    def test_a_service_call_never_loads_the_certificate_store(self):
        # A new opener per call made a new HTTPS context, which reads the system
        # certificate store: about 20 ms of CPU of every Studio call on Windows.
        with _serving() as base, patch.object(ssl.SSLContext, "load_default_certs",
                                              side_effect=AssertionError("certificate store loaded")):
            for _ in range(2):
                self.assertEqual(chat._request_json(base, "/api/protocol", timeout=5), {"ok": True})

    def test_a_redirecting_service_is_still_refused(self):
        with _serving() as base, self.assertRaises(HubFailure) as refused:
            chat._request_json(base, "/moved", timeout=5)
        self.assertEqual((refused.exception.status, refused.exception.error.code), (409, "CHAT_SERVICE_CHANGED"))
