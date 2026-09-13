import ftplib
import json
from zipfile import ZipFile

import pytest

from monkeyfab import send
from monkeyfab.cli import main


ACCESS_CODE = "fake-test-access-code"


class FakeFTPS:
    def __init__(self):
        self.existing_files = {}
        self.size_delta = 0
        self.transfer_error = None
        self.login_error = None
        self.listing_error = None
        self.commands = []
        self.size_requests = []
        self.uploaded_bytes = None
        self.closed = False
        self.protected = False

    def connect(self, *, host, port):
        self.endpoint = (host, port)

    def login(self, username, password):
        self.login_credentials = (username, password)
        if self.login_error:
            raise self.login_error

    def prot_p(self):
        self.protected = True

    def nlst(self, path):
        self.commands.append("NLST " + path)
        if self.listing_error:
            raise self.listing_error
        return list(self.existing_files)

    def size(self, path):
        self.size_requests.append(path)
        if self.uploaded_bytes is not None:
            return len(self.uploaded_bytes) + self.size_delta
        if path not in self.existing_files:
            raise ftplib.error_perm("550 File does not exist")
        return len(self.existing_files[path])

    def storbinary(self, command, stream, *, blocksize):
        self.commands.append(command)
        self.uploaded_bytes = stream.read()
        if self.transfer_error:
            raise self.transfer_error
        return "226 Transfer complete"

    def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def forbid_real_client(monkeypatch):
    def forbidden_client(timeout):
        pytest.fail("validation must not create a network client")

    monkeypatch.setattr(send, "_ftp_client", forbidden_client)


@pytest.fixture
def sliced_job(tmp_path):
    source = tmp_path / "model.gcode.3mf"
    with ZipFile(source, "w") as archive:
        archive.writestr("Metadata/plate_1.gcode", b"; sliced test plate\nG1 X10 Y20\n")
        archive.writestr("Metadata/plate_2.gcode", b"; second sliced test plate\n")
    return source


@pytest.fixture
def fake_ftp(monkeypatch):
    client = FakeFTPS()
    monkeypatch.setattr(send, "_ftp_client", lambda timeout: client)
    return client


def test_send_uploads_original_archive_and_confirms_remote_size(sliced_job, fake_ftp):
    original = sliced_job.read_bytes()
    result = send.send_file(
        sliced_job, host="192.0.2.10", access_code=ACCESS_CODE,
        remote_name="renamed.gcode.3mf",
    )

    assert fake_ftp.uploaded_bytes == original == sliced_job.read_bytes()
    assert fake_ftp.size_requests == ["/renamed.gcode.3mf"]
    assert fake_ftp.endpoint == ("192.0.2.10", 990)
    assert fake_ftp.protected
    assert fake_ftp.commands == ["NLST /", "STOR /renamed.gcode.3mf"]
    assert fake_ftp.closed
    assert result["status"] == "uploaded"
    assert result["print_started"] is False
    assert result["bytes"] == len(original)


@pytest.mark.parametrize(("existing_name", "existing_size"), [
    ("model.gcode.3mf", 0),
    ("/model.gcode.3mf", 123),
    ("/MODEL.GCODE.3MF", 123),
])
def test_existing_remote_file_is_not_overwritten(
    sliced_job, fake_ftp, existing_name, existing_size,
):
    retained = b"x" * existing_size
    fake_ftp.existing_files[existing_name] = retained
    with pytest.raises(ValueError, match="already exists"):
        send.send_file(sliced_job, host="192.0.2.10", access_code=ACCESS_CODE)

    assert fake_ftp.uploaded_bytes is None
    assert not any(command.startswith("STOR ") for command in fake_ftp.commands)
    assert fake_ftp.existing_files[existing_name] == retained
    assert fake_ftp.size_requests == []
    assert fake_ftp.closed


def test_listing_permission_denied_never_attempts_upload(sliced_job, fake_ftp):
    fake_ftp.listing_error = ftplib.error_perm("550 Permission denied " + ACCESS_CODE)
    with pytest.raises(RuntimeError, match="upload was not confirmed") as caught:
        send.send_file(sliced_job, host="192.0.2.10", access_code=ACCESS_CODE)

    assert ACCESS_CODE not in str(caught.value)
    assert fake_ftp.uploaded_bytes is None
    assert not any(command.startswith("STOR ") for command in fake_ftp.commands)
    assert fake_ftp.size_requests == []
    assert fake_ftp.closed


@pytest.mark.parametrize("failure", ["transfer-timeout", "remote-size", "login"])
def test_failed_upload_is_unconfirmed_closed_and_redacted(sliced_job, fake_ftp, failure):
    if failure == "transfer-timeout":
        fake_ftp.transfer_error = TimeoutError("upstream leaked " + ACCESS_CODE)
    elif failure == "remote-size":
        fake_ftp.size_delta = -1
    else:
        fake_ftp.login_error = ftplib.error_perm("530 Invalid credential " + ACCESS_CODE)

    with pytest.raises(RuntimeError, match="upload was not confirmed") as caught:
        send.send_file(sliced_job, host="192.0.2.10", access_code=ACCESS_CODE)

    assert ACCESS_CODE not in str(caught.value)
    assert fake_ftp.closed
    if failure != "login":
        assert "may remain" in str(caught.value)


@pytest.mark.parametrize("contents", ["bad-zip", "unsliced", "empty-gcode"])
def test_invalid_or_unsliced_archives_fail_before_connecting(tmp_path, contents):
    source = tmp_path / "invalid.gcode.3mf"
    if contents == "bad-zip":
        source.write_bytes(b"this is not a zip archive")
    else:
        with ZipFile(source, "w") as archive:
            if contents == "unsliced":
                archive.writestr("3D/3dmodel.model", "<model/>")
            else:
                archive.writestr("Metadata/plate_1.gcode", b"")

    with pytest.raises(ValueError, match="archive"):
        send.send_file(source, host="192.0.2.10", access_code=ACCESS_CODE)


@pytest.mark.parametrize("remote_name", [
    "../outside.gcode.3mf",
    "nested\\outside.gcode.3mf",
    "safe.gcode.3mf\r\nDELE victim.gcode.3mf",
])
def test_remote_paths_and_ftp_command_injection_fail_before_connecting(sliced_job, remote_name):
    with pytest.raises(ValueError, match="plain .gcode.3mf filename"):
        send.send_file(
            sliced_job, host="192.0.2.10", access_code=ACCESS_CODE,
            remote_name=remote_name,
        )


def test_dry_run_needs_no_access_code_or_client(sliced_job):
    original = sliced_job.read_bytes()
    result = send.send_file(sliced_job, host="192.0.2.10", dry_run=True)

    assert result["status"] == "validated"
    assert result["print_started"] is False
    assert result["plates"] == [1, 2]
    assert sliced_job.read_bytes() == original


def test_cli_uses_selected_access_code_environment_and_emits_json(
    sliced_job, fake_ftp, monkeypatch, capsys,
):
    monkeypatch.setenv("BAMBU_ACCESS_CODE", "unused-default-test-code")
    monkeypatch.setenv("MONKEYFAB_TEST_ACCESS_CODE", ACCESS_CODE)
    assert main([
        "send", str(sliced_job), "--host", "192.0.2.10",
        "--access-code-env", "MONKEYFAB_TEST_ACCESS_CODE", "--json",
    ]) == 0

    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert fake_ftp.login_credentials == ("bblp", ACCESS_CODE)
    assert result["status"] == "uploaded"
    assert result["print_started"] is False
    assert ACCESS_CODE not in captured.out + captured.err


def test_cli_dry_run_json_succeeds_without_credentials(sliced_job, monkeypatch, capsys):
    monkeypatch.delenv("BAMBU_ACCESS_CODE", raising=False)
    assert main([
        "send", str(sliced_job), "--host", "192.0.2.10", "--dry-run", "--json",
    ]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "validated"


@pytest.mark.parametrize("failure", ["missing-access-code", "transfer-timeout"])
def test_cli_send_failure_exits_two_without_success_json_or_secret(
    sliced_job, fake_ftp, monkeypatch, capsys, failure,
):
    if failure == "missing-access-code":
        monkeypatch.delenv("MONKEYFAB_TEST_ACCESS_CODE", raising=False)
    else:
        monkeypatch.setenv("MONKEYFAB_TEST_ACCESS_CODE", ACCESS_CODE)
        fake_ftp.transfer_error = TimeoutError("upstream leaked " + ACCESS_CODE)

    assert main([
        "send", str(sliced_job), "--host", "192.0.2.10",
        "--access-code-env", "MONKEYFAB_TEST_ACCESS_CODE", "--json",
    ]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("monkeyfab: ")
    assert ACCESS_CODE not in captured.err
