"""Upload a sliced Bambu job over LAN FTPS without issuing a print command."""

from __future__ import annotations

import ftplib
import ipaddress
import math
from pathlib import Path, PurePosixPath
import re
from zipfile import BadZipFile, ZipFile


def _ftp_client(timeout: float):
    try:
        # Pinned upstream implementation supplies implicit TLS and data-session reuse.
        from bambulabs_api.ftp_client import ImplicitFTP_TLS
    except ImportError as exc:
        raise RuntimeError('Send requires the optional dependency: pip install -e ".[send]"') from exc
    return ImplicitFTP_TLS(timeout=timeout)


def inspect_job(source: Path, remote_name: str | None = None) -> dict:
    """Read a sliced archive without extracting or changing it."""
    if not source.is_file():
        raise ValueError(f"input file does not exist: {source}")
    if not source.name.lower().endswith(".gcode.3mf"):
        raise ValueError("send requires a sliced .gcode.3mf exported from Bambu Studio")
    name = source.name if remote_name is None else remote_name
    if (not name.lower().endswith(".gcode.3mf") or name.startswith(".")
            or any(character in name for character in '/\\:*?"<>|')
            or any(ord(character) < 32 or ord(character) == 127 for character in name)):
        raise ValueError("remote name must be a plain .gcode.3mf filename, without paths or control characters")
    try:
        with ZipFile(source) as archive:
            plates = sorted({
                int(match.group(1)) for entry in archive.infolist()
                if (match := re.fullmatch(r"Metadata/plate_([1-9][0-9]*)\.gcode", entry.filename))
                and entry.file_size > 0
            })
            if not plates:
                raise ValueError("archive contains no sliced Metadata/plate_N.gcode; export the sliced plate first")
            if archive.testzip() is not None:
                raise ValueError("sliced archive failed its ZIP integrity check")
    except (BadZipFile, RuntimeError, NotImplementedError) as exc:
        raise ValueError("input is not a readable, intact sliced .gcode.3mf archive") from exc
    return {"file": str(source), "remote_path": "/" + name,
            "bytes": source.stat().st_size, "plates": plates}


def send_file(source: Path, *, host: str, access_code: str | None = None,
              remote_name: str | None = None, timeout: float = 30.0,
              dry_run: bool = False) -> dict:
    """Validate, upload once, and confirm remote size. No MQTT or print API is used."""
    job = inspect_job(source, remote_name)
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]*", host):
            raise ValueError("host must be the printer IP address or hostname, without a URL or port") from None
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be a positive number of seconds")
    result = {**job, "host": host, "status": "validated", "print_started": False}
    if dry_run:
        return result
    if not access_code or any(character.isspace() for character in access_code):
        raise ValueError("printer access code is missing or invalid; supply it through the selected environment variable")

    ftp = _ftp_client(timeout)
    uploading = False
    try:
        ftp.connect(host=host, port=990)
        ftp.login("bblp", access_code)
        ftp.prot_p()
        # A 550 SIZE reply can mean permission denied, not just a missing file.
        # Require a successful directory listing before choosing a new destination.
        existing_names = {PurePosixPath(name).name.casefold() for name in ftp.nlst("/")}
        if PurePosixPath(job["remote_path"]).name.casefold() in existing_names:
            raise ValueError("a file with this name already exists on the printer; choose --remote-name")
        with source.open("rb") as stream:
            uploading = True
            response = ftp.storbinary("STOR " + job["remote_path"], stream, blocksize=32768)
        remote_size = ftp.size(job["remote_path"])
    except ftplib.all_errors as exc:
        # Never echo an upstream server response containing credentials or file content.
        detail = " A partial or complete file may remain on the printer; check it before retrying." if uploading else ""
        raise RuntimeError(
            f"FTPS upload was not confirmed ({type(exc).__name__}). "
            "Check the printer address, LAN access setting, access code and storage." + detail
        ) from None
    finally:
        ftp.close()
    if response[:3] not in {"226", "250"} or remote_size != job["bytes"]:
        raise RuntimeError(
            "FTPS upload was not confirmed: transfer completion or remote file size did not match. "
            "A partial or complete file may remain on the printer; check it before retrying."
        )
    return {**result, "status": "uploaded"}
