"""The conversational agent's model-export tool uses the bound backend."""
import unittest
from unittest.mock import patch
from monkeyhub_api import chat
from monkeyhub_api.models import HubFailure


class ModelExportChatTests(unittest.TestCase):
    def setUp(self):
        self.session = {"projectId":"demo", "projectDir":"C:/projects/demo", "status":"running"}
        self.binding = patch.object(chat, "_bound_studio", return_value=("http://127.0.0.1:8791", self.session))
        self.binding.start()
        self.addCleanup(self.binding.stop)

    def test_uploaded_conversion_transfers_exact_attachment_to_job(self):
        calls = []
        upload = {"fileName":"source.glb", "contentBase64":"ZXhhY3Q=", "attachmentId":"22222222-2222-4222-8222-222222222222"}
        def request(base, path, method="GET", body=None, **kwargs):
            calls.append((base,path,method,body))
            if path.endswith("/model-source"):
                return upload
            return {"jobId":"job-1", "statusPath":"/api/exports/export-1"}
        with patch.object(chat, "_request_json", side_effect=request):
            result = chat.call_tool("http://127.0.0.1:8790", "11111111-1111-4111-8111-111111111111", "studio_request", {
                "method":"POST", "path":"/api/exports", "body":{"targetFormat":"3dm", "attachmentId":"22222222-2222-4222-8222-222222222222"}})
        self.assertEqual(result["jobId"],"job-1")
        self.assertIn("/sessions/11111111-1111-4111-8111-111111111111/attachments/22222222-2222-4222-8222-222222222222/model-source",calls[0][1])
        self.assertEqual(calls[1][3],{"targetFormat":"3dm","upload":upload})
        self.assertIn("/studio/api/exports",calls[1][1])

    def test_conflicting_upload_and_project_are_not_silently_resolved(self):
        with self.assertRaises(HubFailure) as caught:
            chat.call_tool("http://127.0.0.1:8790", "11111111-1111-4111-8111-111111111111", "studio_request", {
                "method":"POST", "path":"/api/exports", "body":{"targetFormat":"glb", "attachmentId":"22222222-2222-4222-8222-222222222222",
                "projectRevision":{"runId":"run-1","stateDigest":"a"*64}}})
        self.assertEqual(caught.exception.error.code,"EXPORT_SOURCE_AMBIGUOUS")

    def test_only_success_has_download_link(self):
        for status in ("running","failed","succeeded"):
            reply = {"status":status,"downloadPath":"/api/exports/export-1/bytes"}
            with patch.object(chat,"_request_json",return_value=reply):
                result = chat.call_tool("http://127.0.0.1:8790","11111111-1111-4111-8111-111111111111","studio_request",{
                    "path":"/api/exports/export-1"})
            self.assertEqual("downloadUrl" in result,status == "succeeded")
            if status == "succeeded":
                self.assertEqual(result["downloadUrl"],"http://127.0.0.1:8791/api/exports/export-1/bytes")

    def test_current_project_revision_passes_unchanged(self):
        body = {"targetFormat":"skp", "projectRevision":{"runId":"run-1", "stateDigest":"a"*64}}
        with patch.object(chat,"_request_json",return_value={"jobId":"j"}) as request:
            chat.call_tool("http://127.0.0.1:8790","11111111-1111-4111-8111-111111111111","studio_request",{
                "method":"POST","path":"/api/exports","body":body})
        self.assertEqual(request.call_args.args[3],body)

    def test_schema_exposes_attachment_selection_without_base64(self):
        document = {'paths':{'/api/exports':{'post':{'requestBody':{'content':{
            'application/json':{'schema':{'$ref':'#/components/schemas/ModelExportRequest'}}}}}}},
            'components':{'schemas':{'ModelExportRequest':{'properties':{'upload':{'type':'object'},
                'targetFormat':{'type':'string'}}}}}}
        with patch.object(chat,'_request_json',return_value=document):
            result = chat.call_tool('http://127.0.0.1:8790','11111111-1111-4111-8111-111111111111','studio_schema',{
                'method':'POST','path':'/api/exports'})
        properties = result['components']['schemas']['ModelExportRequest']['properties']
        self.assertIn('attachmentId',properties)
        self.assertNotIn('upload',properties)
