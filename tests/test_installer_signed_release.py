"""Signed installation behaviour of the MonkeyHub installer.

These tests drive ``apps/monkeyhub/installer/install.ps1`` end to end against a
small but complete candidate fixture: a package tree, the candidate ZIP it was
extracted from, the ``ReleaseManifest@1`` written by the packaging owner, and a
detached CMS signature made with an ephemeral key that exists only inside this
process and its temporary directory. No machine certificate store, no user
credential and no network are involved.

What is being proven is a chain, not one check: a pinned publisher signature over
the manifest bytes, the manifest bound to this archive, and the archive bound to
every file of the tree that actually becomes the installation. A test that only
showed a valid signature over an unrelated manifest would prove nothing, so every
negative case asserts the installer's own single-line refusal, names the branch it
expects, and checks that nothing was installed or staged.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "apps/monkeyhub/installer/install.ps1"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools import package_monkeyapps as builder  # noqa: E402

try:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.serialization import pkcs7
    from cryptography.x509.oid import NameOID
except ImportError:  # pragma: no cover - turned into a failure by InstallerSourceTests
    x509 = None


COMMIT = "c" * 40
BUILD_INFO = {
    "sourceCommit": COMMIT, "target": "windows-x64", "channel": "candidate",
    "pythonVersion": "3.13.15",
    "pythonUrl": "https://www.python.org/ftp/python/3.13.15/python-3.13.15-embed-amd64.zip",
    "pythonSha256": "d" * 64,
    "runtimeInventory": {
        "nodeVersion": "v24.14.0",
        "pythonRequirements": {"path": "_runtime/requirements-lock.txt", "sha256": "e" * 64},
        "acpAdapter": {"name": "@agentclientprotocol/codex-acp", "version": "1.11.0",
                       "packageLockSha256": "f" * 64},
        "frontends": {}, "externalDependencies": [],
    },
}
# install.ps1 refuses an incomplete extraction before anything else; the fixture
# ships exactly the files it names so a refusal always comes from the signature
# or the binding under test.
PACKAGE_FILES = (
    "OPEN_MONKEYHUB.cmd", "_runtime/python/python.exe", "apps/monkeyhub/run.py",
    "apps/monkeyhub/launch-hub.ps1", "apps/monkeyhub/web/dist/index.html",
    "apps/archflow-studio/web/dist/index.html",
    "apps/monkeyfab/src/monkeyfab/__main__.py", "apps/monkeyfab/pyproject.toml",
)
SUPPORTED_HOSTS = (("PowerShell 7", "pwsh"), ("Windows PowerShell 5.1", "powershell"))


def powershell_hosts() -> list[tuple[str, str]]:
    """The supported Windows hosts actually present, newest first."""
    return [(label, shutil.which(name)) for label, name in SUPPORTED_HOSTS if shutil.which(name)]


def normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def refusal_line(result: subprocess.CompletedProcess) -> str:
    """The installer's own one-line reason, which no host wraps or reformats."""
    for line in result.stdout.splitlines():
        if line.startswith("Refused: "):
            return line
    return ""


def evidence_of(result: subprocess.CompletedProcess) -> dict:
    return json.JSONDecoder().raw_decode(result.stdout.lstrip())[0]


@unittest.skipUnless(sys.platform == "win32", "Windows installer behaviour")
@unittest.skipIf(x509 is None, "cryptography is needed to make an ephemeral signing key")
@unittest.skipUnless(powershell_hosts(), "no supported PowerShell host is available")
class SignedInstallationTests(unittest.TestCase):
    """Drive the installer against a signed fixture and its failure modes."""

    @classmethod
    def setUpClass(cls) -> None:
        # One ephemeral 2048-bit key pair per class; it never leaves this process.
        cls.publisher = cls.make_signer("MonkeyHub release test publisher")
        cls.impostor = cls.make_signer("Someone else entirely")
        cls.host = powershell_hosts()[0][1]

    @staticmethod
    def make_signer(common_name: str):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
        now = datetime.datetime.now(datetime.timezone.utc)
        certificate = (
            x509.CertificateBuilder()
            .subject_name(name).issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(minutes=5))
            .not_valid_after(now + datetime.timedelta(days=1))
            .sign(key, hashes.SHA256())
        )
        return key, certificate

    @staticmethod
    def fingerprint(signer, algorithm: str = "sha256") -> str:
        der = signer[1].public_bytes(serialization.Encoding.DER)
        return hashlib.new(algorithm, der).hexdigest().upper()

    @staticmethod
    def sign_detached(payload: bytes, *signers, digest=None, detached: bool = True) -> bytes:
        """A detached CMS over exactly the bytes given, by one signer or more."""
        options = [pkcs7.PKCS7Options.Binary, pkcs7.PKCS7Options.NoCapabilities]
        if detached:
            options.append(pkcs7.PKCS7Options.DetachedSignature)
        builder = pkcs7.PKCS7SignatureBuilder().set_data(payload)
        for key, certificate in signers:
            builder = builder.add_signer(certificate, key, digest or hashes.SHA256())
        return builder.sign(serialization.Encoding.DER, options)

    def setUp(self) -> None:
        # A short path: install.ps1 refuses legacy Win32 path lengths on purpose.
        temporary = tempfile.TemporaryDirectory(prefix="mh-sig-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.output = self.root / "out"
        self.output.mkdir()
        self.installed = self.root / "installed"
        self.shortcuts = self.root / "desktop"
        self.shortcuts.mkdir()
        self.environment = dict(os.environ, LOCALAPPDATA=str(self.root / "local"))
        self.prefix = f"MonkeyHub-{COMMIT[:12]}-windows-x64"
        self.package = self.build_package(self.prefix, BUILD_INFO)
        self.archive = self.build_archive(self.package, self.prefix)
        self.manifest, self.signature = self.publish(self.package, self.archive, self.prefix, BUILD_INFO)

    def build_package(self, prefix: str, build_info: dict) -> Path:
        """An extracted candidate tree carrying the installer under test."""
        package = self.root / prefix
        for relative in PACKAGE_FILES:
            path = package / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(f"fixture {relative} - never executed\n".encode("ascii"))
        (package / "source-version.txt").write_text(build_info["sourceCommit"], encoding="utf-8")
        (package / "build-info.json").write_text(json.dumps(build_info), encoding="utf-8")
        (package / builder.SBOM_NAME).write_text(
            json.dumps({"bomFormat": "CycloneDX", "specVersion": builder.SBOM_SPEC_VERSION,
                        "components": []}), encoding="utf-8")
        script = package / "apps/monkeyhub/installer/install.ps1"
        script.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(INSTALLER, script)
        return package

    def build_archive(self, package: Path, prefix: str) -> Path:
        """The candidate ZIP the package was extracted from, member for member."""
        archive = self.output / f"{prefix}-candidate.zip"
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as opened:
            for path in sorted(package.rglob("*")):
                if path.is_file():
                    opened.write(path, (Path(prefix) / path.relative_to(package)).as_posix())
        return archive

    def publish(self, package: Path, archive: Path, prefix: str, build_info: dict,
                signer=None) -> tuple[Path, Path]:
        """Release evidence beside the archive, plus a detached publisher signature."""
        sbom = self.output / f"{prefix}.cyclonedx.json"
        shutil.copy2(package / builder.SBOM_NAME, sbom)
        checksum = self.output / f"{archive.name}.sha256"
        checksum.write_text(f"{builder.sha256(archive)}  {archive.name}\n", encoding="utf-8")
        manifest = self.output / f"{archive.name}.release-manifest.json"
        manifest.write_text(json.dumps(builder.release_manifest(
            build_info, build_info["sourceCommit"][:12], prefix, package / "build-info.json",
            archive, (archive, checksum, sbom), sbom), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
        self.assertEqual(builder.verify_release(manifest), [],
                         "the fixture must be a release the packaging owner accepts")
        signature = Path(f"{manifest}.p7s")
        signature.write_bytes(self.sign_detached(manifest.read_bytes(), signer or self.publisher))
        return manifest, signature

    def signed_arguments(self, *, manifest: Path | None = None, signature: Path | None = None,
                         archive: Path | None = None, thumbprint: str | None = None) -> list[str]:
        return [
            "-RequireSignedRelease",
            "-ReleaseManifest", str(manifest or self.manifest),
            "-ReleaseSignature", str(signature or self.signature),
            "-ReleaseArchive", str(archive or self.archive),
            "-ExpectedPublisherThumbprint", thumbprint or self.fingerprint(self.publisher),
        ]

    def verify_only_arguments(self, *, thumbprint: str | None = None) -> list[str]:
        return [
            "-VerifyReleaseManifest",
            "-ReleaseManifest", str(self.manifest),
            "-ReleaseSignature", str(self.signature),
            "-ExpectedPublisherThumbprint", thumbprint or self.fingerprint(self.publisher),
        ]

    def run_installer(self, *arguments: str, host: str | None = None,
                      package: Path | None = None) -> subprocess.CompletedProcess:
        script = (package or self.package) / "apps/monkeyhub/installer/install.ps1"
        # An explicit encoding, because one refusal quotes a platform message
        # rather than this script's own ASCII: on a localized Windows the hosts
        # emit it in the console code page while Python would decode with the
        # ANSI one, and the mismatch turns a real assertion into a decode error.
        # The tokens asserted below are ASCII and survive any replacement.
        return subprocess.run(
            [host or self.host, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script),
             *arguments],
            env=self.environment, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=180)

    def install_signed(self, *extra: str, destination: Path | None = None,
                       host: str | None = None, **overrides) -> subprocess.CompletedProcess:
        target = destination or self.installed
        return self.run_installer("-InstallDirectory", str(target),
                                  *self.signed_arguments(**overrides), *extra, host=host)

    def assertRefused(self, result: subprocess.CompletedProcess, token: str) -> None:
        """A refusal must name its own reason and leave nothing behind.

        The token is matched against the single unwrapped line the installer
        prints itself, not against the host's reformatted error record, so a
        parse error or an unrelated exception cannot satisfy it.
        """
        combined = normalise(result.stdout + result.stderr)
        self.assertNotEqual(result.returncode, 0, combined)
        reason = refusal_line(result)
        self.assertTrue(reason, f"the installer printed no single-line refusal: {combined}")
        self.assertIn(token, reason)
        self.assertNotIn("Installed MonkeyHub source", combined)
        # Refusing after the copy or after adopting an existing installation
        # would still leave these tells, whatever the exit code says.
        self.assertNotIn("This build is already installed", combined)
        self.assertNotIn("Incomplete installation files remain", combined)
        self.assertEqual(list(self.root.rglob(".mh-*")), [])

    # --- the signed path -------------------------------------------------

    def test_signed_install_binds_manifest_to_this_package_before_installing(self) -> None:
        result = self.install_signed()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((self.installed / "OPEN_MONKEYHUB.cmd").is_file())
        evidence = evidence_of(result)
        self.assertEqual(evidence["schema"], "ReleaseSignatureEvidence@1")
        self.assertEqual(evidence["signerThumbprint"], self.fingerprint(self.publisher))
        self.assertEqual(evidence["thumbprintAlgorithm"], "sha256")
        self.assertEqual(evidence["chainValidation"], "pinned-signature-only")
        self.assertEqual(evidence["revocationCheck"], "not-demonstrated")
        self.assertEqual(evidence["boundSourceCommit"], COMMIT)
        self.assertEqual(evidence["archiveSha256"], builder.sha256(self.archive))
        # Every file of the package is covered, not only the two the manifest names.
        files = sum(1 for path in self.package.rglob("*") if path.is_file())
        self.assertEqual(evidence["boundPackageFiles"], files)
        # The signature proves who wrote the manifest; the build is still a candidate.
        self.assertEqual(evidence["manifestTrustStatus"], "candidate-unsigned")
        self.assertIn("bound to the named candidate archive", evidence["statement"])
        # What was verified is the tree that became the installation, not only
        # the staging copy it was read from.
        self.assertIn(f"{files} installed files verified", normalise(result.stdout))

    def test_a_sha1_publisher_pin_is_accepted_and_reported_as_sha1(self) -> None:
        for label, executable in powershell_hosts():
            with self.subTest(host=label):
                destination = self.installed / re.sub(r"\W+", "-", label)
                result = self.install_signed(destination=destination, host=executable,
                                             thumbprint=self.fingerprint(self.publisher, "sha1"))
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                evidence = evidence_of(result)
                self.assertEqual(evidence["thumbprintAlgorithm"], "sha1")
                self.assertEqual(evidence["signerThumbprint"],
                                 self.fingerprint(self.publisher, "sha1"))

    def test_requiring_a_trusted_chain_refuses_an_untrusted_publisher(self) -> None:
        # A self-signed ephemeral publisher has no trusted chain, so requiring one
        # must refuse rather than quietly downgrade to the pinned check.
        result = self.install_signed("-RequireTrustedPublisherChain")
        self.assertRefused(result, "Release signature verification failed")
        self.assertNotIn("pinned-signature-only", result.stdout)
        self.assertFalse(self.installed.exists())

    # --- refusals that must happen before any mutation -------------------

    def test_changed_manifest_stops_before_touching_an_existing_installation(self) -> None:
        shutil.copytree(self.package, self.installed)
        before = {path: path.read_bytes() for path in self.installed.rglob("*") if path.is_file()}
        text = self.manifest.read_text(encoding="utf-8")
        self.manifest.write_text(text.replace('"channel": "candidate"', '"channel": "release"  '),
                                 encoding="utf-8")
        result = self.install_signed("-CreateDesktopShortcut", "-DesktopDirectory", str(self.shortcuts))
        self.assertRefused(result, "Release signature verification failed")
        # The identical-build branch would otherwise have made a shortcut here.
        self.assertEqual(list(self.shortcuts.iterdir()), [])
        self.assertEqual({path: path.read_bytes()
                          for path in self.installed.rglob("*") if path.is_file()}, before)

    def test_a_tampered_existing_installation_is_not_adopted_shortcut_or_launched(self) -> None:
        # The package on disk is authentic; the already-installed copy is not.
        # Verifying only the package would shortcut and launch the tampered file.
        shutil.copytree(self.package, self.installed)
        (self.installed / "OPEN_MONKEYHUB.cmd").write_bytes(b"replaced after installation\n")
        result = self.install_signed("-CreateDesktopShortcut", "-DesktopDirectory", str(self.shortcuts),
                                     "-OpenHub")
        self.assertRefused(result, "OPEN_MONKEYHUB.cmd differs from it")
        self.assertEqual(list(self.shortcuts.iterdir()), [])
        self.assertNotIn("ReleaseSignatureEvidence@1", result.stdout,
                         "a refused installation must not report that installed files were verified")

    def test_changed_package_file_is_rejected_although_the_signature_is_valid(self) -> None:
        target = self.package / "apps/monkeyhub/run.py"
        target.write_bytes(b"fixture apps/monkeyhub/run.py - never executed\n" + b"backdoor\n")
        result = self.install_signed()
        self.assertRefused(result, "apps/monkeyhub/run.py differs from it")
        self.assertFalse(self.installed.exists())

    def test_package_missing_a_signed_file_is_rejected(self) -> None:
        # Not one of the files install.ps1 already requires, so the refusal can
        # only come from the archive binding rather than the completeness check.
        (self.package / builder.SBOM_NAME).unlink()
        result = self.install_signed()
        self.assertRefused(result, "1 signed file(s) are missing")
        self.assertFalse(self.installed.exists())

    def test_extra_package_file_absent_from_the_signed_archive_is_rejected(self) -> None:
        (self.package / "apps/monkeyhub/extra.py").write_bytes(b"added after signing\n")
        result = self.install_signed()
        self.assertRefused(result, "apps/monkeyhub/extra.py is not in it")
        self.assertFalse(self.installed.exists())

    def test_a_directory_junction_added_to_the_package_is_rejected(self) -> None:
        # A junction is not a file, so a plain file walk never sees what it adds,
        # while a recursive copy installs the whole target behind it.
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "payload.dll").write_bytes(b"payload\n")
        link = self.package / "apps/monkeyhub/plugins"
        created = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)],
                                 capture_output=True, text=True)
        if created.returncode != 0:
            self.skipTest(f"no directory junction here: {created.stdout}{created.stderr}")
        self.addCleanup(lambda: link.is_dir() and link.rmdir())
        result = self.install_signed()
        self.assertRefused(result, "apps/monkeyhub/plugins is a reparse point")
        self.assertFalse(self.installed.exists())

    def test_changed_candidate_archive_is_rejected(self) -> None:
        self.archive.write_bytes(self.archive.read_bytes() + b"\x00")
        result = self.install_signed()
        self.assertRefused(result, "candidate archive does not match the signed manifest")
        self.assertFalse(self.installed.exists())

    def test_a_correctly_signed_manifest_for_another_release_is_rejected(self) -> None:
        other_commit = "a" * 40
        other_prefix = f"MonkeyHub-{other_commit[:12]}-windows-x64"
        other_info = dict(BUILD_INFO, sourceCommit=other_commit)
        other_package = self.build_package(other_prefix, other_info)
        other_archive = self.build_archive(other_package, other_prefix)
        manifest, signature = self.publish(other_package, other_archive, other_prefix, other_info)
        result = self.install_signed(manifest=manifest, signature=signature, archive=other_archive)
        self.assertRefused(result, "signed manifest describes source commit")
        self.assertFalse(self.installed.exists())

    def test_a_changed_build_info_is_rejected_by_its_own_binding(self) -> None:
        info = json.loads((self.package / "build-info.json").read_text(encoding="utf-8"))
        info["channel"] = "release"
        (self.package / "build-info.json").write_text(json.dumps(info), encoding="utf-8")
        result = self.install_signed()
        self.assertRefused(result, "binds build-info.json to")
        self.assertFalse(self.installed.exists())

    def test_a_manifest_signed_by_another_publisher_is_rejected(self) -> None:
        self.signature.write_bytes(self.sign_detached(self.manifest.read_bytes(), self.impostor))
        result = self.install_signed()
        self.assertRefused(result, "does not match the expected publisher")
        self.assertFalse(self.installed.exists())

    def test_a_signature_over_something_else_is_rejected(self) -> None:
        self.signature.write_bytes(self.sign_detached(b"a different document", self.publisher))
        result = self.install_signed()
        self.assertRefused(result, "Release signature verification failed")
        self.assertFalse(self.installed.exists())

    def test_an_embedded_content_signature_cannot_authenticate_another_manifest(self) -> None:
        # The genuine manifest travels inside the CMS while a forged one is passed
        # as the file. Verifying the embedded copy and then reading the file would
        # hand the whole binding chain to unsigned input, so this pins that the
        # signature is always checked against the bytes the installer will parse.
        genuine = self.manifest.read_bytes()
        forged = json.loads(genuine)
        forged["release"]["sourceCommit"] = "b" * 40
        self.manifest.write_bytes(json.dumps(forged).encode("utf-8"))
        self.signature.write_bytes(self.sign_detached(genuine, self.publisher, detached=False))
        for label, executable in powershell_hosts():
            with self.subTest(host=label):
                result = self.install_signed(host=executable)
                self.assertRefused(result, "Release signature verification failed")
                # Refused at the signature, never at a later binding check: the
                # forged bytes were not accepted as an authentic manifest.
                self.assertNotIn("signed manifest describes source commit",
                                 normalise(result.stdout + result.stderr))
                self.assertFalse(self.installed.exists())

    def test_a_second_signer_beside_the_publisher_is_refused_by_name(self) -> None:
        # The pin only means something if exactly one signer is pinned. Both
        # orderings are refused, and the reason names the signer count rather
        # than reporting the absent single signer's digest as unsupported.
        for label, signers in (("publisher first", (self.publisher, self.impostor)),
                               ("impostor first", (self.impostor, self.publisher))):
            with self.subTest(order=label):
                self.signature.write_bytes(
                    self.sign_detached(self.manifest.read_bytes(), *signers))
                result = self.install_signed()
                self.assertRefused(result, "Expected exactly one embedded release signer; found 2")
                self.assertFalse(self.installed.exists())

    def test_an_unpinned_message_digest_algorithm_is_rejected(self) -> None:
        # SHA-224 stands in for the algorithm this signing toolchain refuses to
        # produce at all: a SHA-1 digest would let a collision on the manifest
        # bytes carry the publisher's own signature. Both take this branch.
        self.signature.write_bytes(
            self.sign_detached(self.manifest.read_bytes(), self.publisher, digest=hashes.SHA224()))
        result = self.install_signed()
        self.assertRefused(result, "unsupported message digest algorithm")
        self.assertFalse(self.installed.exists())

    def test_a_manifest_of_another_schema_is_rejected_even_when_correctly_signed(self) -> None:
        self.manifest.write_text(json.dumps({"schema": "SomethingElse@2"}), encoding="utf-8")
        self.signature.write_bytes(self.sign_detached(self.manifest.read_bytes(), self.publisher))
        result = self.install_signed()
        self.assertRefused(result, "is not a ReleaseManifest@1 document")
        self.assertFalse(self.installed.exists())

    def test_signed_mode_never_falls_back_to_unsigned_on_missing_or_bad_input(self) -> None:
        missing = self.output / "absent.json"
        pin = self.fingerprint(self.publisher)
        needs_all = "needs -ReleaseManifest, -ReleaseSignature, -ReleaseArchive"
        cases = (
            ("no inputs at all", ["-RequireSignedRelease"], needs_all),
            ("no archive", ["-RequireSignedRelease", "-ReleaseManifest", str(self.manifest),
                            "-ReleaseSignature", str(self.signature),
                            "-ExpectedPublisherThumbprint", pin], needs_all),
            ("no publisher pin", ["-RequireSignedRelease", "-ReleaseManifest", str(self.manifest),
                                  "-ReleaseSignature", str(self.signature),
                                  "-ReleaseArchive", str(self.archive)], needs_all),
            ("both modes at once", self.signed_arguments() + ["-VerifyReleaseManifest"],
             "Choose either -VerifyReleaseManifest or -RequireSignedRelease"),
            ("missing signature file", self.signed_arguments(signature=missing),
             "Release signature file was not found"),
            ("missing manifest file", self.signed_arguments(manifest=missing),
             "Release manifest file was not found"),
            ("missing archive file", self.signed_arguments(archive=missing),
             "Release archive file was not found"),
            ("thumbprint is not hexadecimal", self.signed_arguments(thumbprint="not-a-thumbprint"),
             "must be 64 hexadecimal characters (SHA-256) or 40 (SHA-1)"),
            ("thumbprint of an unsupported length", self.signed_arguments(thumbprint="AB" * 25),
             "must be 64 hexadecimal characters (SHA-256) or 40 (SHA-1)"),
            ("truncated thumbprint", self.signed_arguments(thumbprint="AB" * 19 + "C"),
             "must be 64 hexadecimal characters (SHA-256) or 40 (SHA-1)"),
        )
        for label, arguments, token in cases:
            with self.subTest(case=label):
                result = self.run_installer("-InstallDirectory", str(self.installed), *arguments)
                self.assertRefused(result, token)
                self.assertFalse(self.installed.exists())

    # --- the unsigned candidate path stays visible and unchanged ---------

    def test_unsigned_installation_stays_labelled_as_an_unsigned_candidate(self) -> None:
        result = self.run_installer("-InstallDirectory", str(self.installed))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((self.installed / "OPEN_MONKEYHUB.cmd").is_file())
        output = normalise(result.stdout)
        self.assertIn("candidate-unsigned", output)
        self.assertIn("No publisher signature was verified", output)
        self.assertNotIn("ReleaseSignatureEvidence@1", output)

    def test_every_signature_input_without_an_explicit_mode_is_refused(self) -> None:
        token = "require -RequireSignedRelease or -VerifyReleaseManifest"
        for argument in (["-ReleaseManifest", str(self.manifest)],
                         ["-ReleaseSignature", str(self.signature)],
                         ["-ReleaseArchive", str(self.archive)],
                         ["-ExpectedPublisherThumbprint", self.fingerprint(self.publisher)],
                         ["-RequireTrustedPublisherChain"]):
            with self.subTest(argument=argument[0]):
                result = self.run_installer("-InstallDirectory", str(self.installed), *argument)
                self.assertRefused(result, token)
                self.assertFalse(self.installed.exists())

    # --- the verification-only CLI ---------------------------------------

    def test_verification_only_reports_evidence_and_installs_nothing(self) -> None:
        result = self.run_installer(*self.verify_only_arguments())
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        evidence = evidence_of(result)
        self.assertEqual(evidence["schema"], "ReleaseSignatureEvidence@1")
        self.assertEqual(evidence["signerThumbprint"], self.fingerprint(self.publisher))
        self.assertIsNone(evidence["archiveSha256"])
        self.assertEqual(evidence["boundPackageFiles"], 0)
        self.assertIn("not bound to any installed package", evidence["statement"])
        self.assertFalse(self.installed.exists())

    def test_verification_only_refuses_a_signature_from_another_publisher(self) -> None:
        self.signature.write_bytes(self.sign_detached(self.manifest.read_bytes(), self.impostor))
        result = self.run_installer(*self.verify_only_arguments())
        self.assertRefused(result, "does not match the expected publisher")
        self.assertNotIn("ReleaseSignatureEvidence@1", result.stdout)

    def test_verification_only_refuses_an_archive_it_would_not_bind(self) -> None:
        result = self.run_installer(*self.verify_only_arguments(),
                                    "-ReleaseArchive", str(self.archive))
        self.assertRefused(result, "-VerifyReleaseManifest binds no package")

    # --- both supported Windows hosts ------------------------------------

    def test_supported_powershell_hosts_accept_and_refuse_alike(self) -> None:
        hosts = powershell_hosts()
        self.assertEqual([label for label, _ in hosts], [label for label, _ in SUPPORTED_HOSTS])
        for label, executable in hosts:
            with self.subTest(host=label):
                destination = self.installed / re.sub(r"\W+", "-", label)
                accepted = self.install_signed(destination=destination, host=executable)
                self.assertEqual(accepted.returncode, 0, accepted.stdout + accepted.stderr)
                self.assertTrue((destination / "OPEN_MONKEYHUB.cmd").is_file())
                refused = self.run_installer(
                    "-InstallDirectory", str(destination / "rejected"),
                    *self.signed_arguments(thumbprint=self.fingerprint(self.impostor)),
                    host=executable)
                self.assertRefused(refused, "does not match the expected publisher")
                self.assertFalse((destination / "rejected").exists())


class InstallerSourceTests(unittest.TestCase):
    """Properties of the shipped script, and of the environment testing it."""

    def test_installer_is_ascii_and_carries_no_signing_material(self) -> None:
        text = INSTALLER.read_text(encoding="ascii")
        self.assertIn("ReleaseSignatureEvidence@1", text)
        self.assertIn("CheckSignature", text)
        self.assertNotIn("BEGIN PRIVATE KEY", text)
        self.assertNotIn("BEGIN RSA PRIVATE KEY", text)
        # The shipped installer verifies; it does not mint its own key material.
        self.assertNotIn("CreateSelfSigned", text)

    def test_this_windows_machine_can_actually_run_the_signed_release_suite(self) -> None:
        """A degraded environment must fail here rather than skip the suite green."""
        if sys.platform != "win32":
            self.skipTest("the installer is a Windows artifact")
        self.assertIsNotNone(x509, "cryptography is required to exercise the verifier")
        self.assertEqual([label for label, _ in powershell_hosts()],
                         [label for label, _ in SUPPORTED_HOSTS])


if __name__ == "__main__":
    unittest.main()
