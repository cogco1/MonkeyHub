import json
from pathlib import Path
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "apps/monkeyhub/installer/install.ps1"


class ReleaseSignatureSpikeTests(unittest.TestCase):
    def test_installer_exposes_fail_closed_manifest_verification(self):
        text = INSTALLER.read_text(encoding="ascii")
        self.assertIn("ReleaseSignatureEvidence@1", text)
        self.assertIn("ExpectedPublisherThumbprint", text)
        self.assertIn("CheckSignature", text)
        self.assertIn("changed ReleaseManifest", text)
        self.assertNotIn("BEGIN PRIVATE KEY", text)

    def test_detached_signature_self_test_rejects_changed_manifest(self):
        powershell = shutil.which("pwsh") or shutil.which("powershell")
        if powershell is None:
            self.skipTest("PowerShell with System.Security.Cryptography.Pkcs is unavailable")
        completed = subprocess.run(
            [powershell, "-NoProfile", "-File", str(INSTALLER), "-ReleaseSignatureSelfTest"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        evidence = json.loads(completed.stdout)
        self.assertEqual(evidence["schema"], "ReleaseSignatureEvidence@1")
        self.assertRegex(evidence["manifestSha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(evidence["signatureSha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(evidence["signerThumbprint"], r"^[0-9A-F]{40,128}$")
        self.assertEqual(evidence["chainValidation"], "signature-only")


if __name__ == "__main__":
    unittest.main()
