"""The unsigned prerelease channel: this repository's public GitHub releases, read over HTTPS.

The release list is read with its ETag; the update index, release manifest and
patch a release names are size-capped, and a download is SHA-256-checked
against its index before anything uses it. What the checks prove is
consistency, never the publisher (issue #58).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote
import urllib.request


MAX_FEED_BYTES = 8 * 1024 * 1024
REPOSITORY = "cogco1/MonkeyHub"
INDEX_SCHEMA = "MonkeyHubUpdateIndex@1"
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_VERSION = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")


class UpdateCheckError(Exception):
    """A check found nothing usable. Transient failures are retried next time."""

    def __init__(self, detail: str, *, transient: bool = False, version: str | None = None):
        super().__init__(detail)
        self.transient, self.version = transient, version


class UpdateCancelled(Exception):
    """The application is closing; a check stops without judging the release."""


class _HttpsRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not newurl.startswith("https://"):
            raise HTTPError(newurl, code, "A release download may redirect only to HTTPS.", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _read_limited(response, limit: int) -> bytes:
    data = response.read(limit + 1)
    if len(data) > limit:
        raise UpdateCheckError(f"A release answer is larger than {limit} bytes.")
    return data


class ReleaseFeed:
    """Unauthenticated HTTPS reads of this repository's public GitHub releases."""

    API = f"https://api.github.com/repos/{REPOSITORY}/releases?per_page=30"

    def __init__(self, user_agent: str, opener: urllib.request.OpenerDirector | None = None):
        self._user_agent = user_agent
        self._opener = opener or urllib.request.build_opener(_HttpsRedirects())

    def _open(self, url: str, accept: str, extra: dict[str, str] | None = None):
        request = urllib.request.Request(url, headers={"User-Agent": self._user_agent, "Accept": accept, **(extra or {})})
        return self._opener.open(request, timeout=30)

    def releases(self, etag: str | None) -> tuple[str | None, list | None]:
        """The release list and its ETag; None when the cached list is still current (HTTP 304)."""
        extra = {"X-GitHub-Api-Version": "2022-11-28", **({"If-None-Match": etag} if etag else {})}
        try:
            with self._open(self.API, "application/vnd.github+json", extra) as response:
                return response.headers.get("ETag"), json.loads(_read_limited(response, MAX_FEED_BYTES))
        except HTTPError as error:
            if error.code == 304:
                return etag, None
            if error.code in (403, 429):
                raise UpdateCheckError("GitHub's limit for unauthenticated checks is reached; the next check retries.",
                                       transient=True) from error
            raise UpdateCheckError(f"GitHub releases answered HTTP {error.code}.", transient=True) from error
        except (URLError, OSError, ValueError) as error:
            raise UpdateCheckError(f"GitHub releases could not be read: {error}", transient=True) from error

    @staticmethod
    def url(tag: str, name: str) -> str:
        return f"https://github.com/{REPOSITORY}/releases/download/{quote(tag, safe='')}/{quote(name, safe='')}"

    def read(self, tag: str, name: str, size: int) -> bytes:
        """One small release asset of exactly the size its release lists."""
        try:
            with self._open(self.url(tag, name), "application/octet-stream") as response:
                data = _read_limited(response, size)
        except (URLError, OSError) as error:
            raise UpdateCheckError(f"{name} could not be downloaded: {error}", transient=True) from error
        if len(data) != size:
            raise UpdateCheckError(f"{name} ended after {len(data)} of {size} bytes.", transient=True)
        return data

    def download(self, tag: str, name: str, destination: Path, size: int, sha256: str,
                 cancelled: Callable[[], bool]) -> None:
        """Stream one release asset to a new file, refusing more bytes or other bytes than the index states."""
        digest, received = hashlib.sha256(), 0
        try:
            with self._open(self.url(tag, name), "application/octet-stream") as response, destination.open("xb") as output:
                while chunk := response.read(256 * 1024):
                    if cancelled():
                        raise UpdateCancelled()
                    received += len(chunk)
                    if received > size:
                        raise UpdateCheckError(f"{name} is larger than its update index states.")
                    digest.update(chunk)
                    output.write(chunk)
        except (URLError, OSError) as error:
            raise UpdateCheckError(f"{name} could not be downloaded: {error}", transient=True) from error
        if received != size:
            raise UpdateCheckError(f"{name} ended after {received} of {size} bytes.", transient=True)
        if digest.hexdigest() != sha256:
            raise UpdateCheckError(f"{name} does not match the SHA-256 in its update index; it was not used.",
                                   transient=True)


def _version(value: object) -> tuple[int, int, int] | None:
    match = _VERSION.fullmatch(value) if isinstance(value, str) else None
    return (int(match[1]), int(match[2]), int(match[3])) if match else None


def _json_object(data: bytes, label: str) -> dict:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"duplicate key {key!r}")
            result[key] = value
        return result
    try:
        document = json.loads(data, object_pairs_hook=pairs)
    except (ValueError, UnicodeError) as error:
        raise UpdateCheckError(f"{label} is not valid JSON: {error}") from error
    if not isinstance(document, dict):
        raise UpdateCheckError(f"{label} is not a JSON object.")
    return document


def _desktop_releases(raw: object) -> list[dict]:
    """Published desktop prereleases: vMAJOR.MINOR.PATCH tags and their asset sizes."""
    if not isinstance(raw, list):
        raise UpdateCheckError("GitHub returned an unexpected release list.", transient=True)
    rows = []
    for item in raw:
        if not isinstance(item, dict) or item.get("draft") is not False or item.get("prerelease") is not True:
            continue
        tag = item.get("tag_name")
        if not isinstance(tag, str) or not tag.startswith("v") or _version(tag[1:]) is None:
            continue
        assets = {asset["name"]: asset["size"] for asset in item.get("assets") or ()
                  if isinstance(asset, dict) and isinstance(asset.get("name"), str) and type(asset.get("size")) is int}
        rows.append({"tag": tag, "version": tag[1:], "assets": assets})
    return rows


def _entry(value: object, name: str, label: str) -> dict:
    if (not isinstance(value, dict) or value.get("name") != name or type(value.get("size")) is not int
            or value["size"] < 0 or not isinstance(value.get("sha256"), str) or not _SHA256.fullmatch(value["sha256"])):
        raise UpdateCheckError(f"The update index names no valid {label}.")
    return value


def _update_index(data: bytes, version: str) -> dict:
    document = _json_object(data, "The update index")
    commit = document.get("releaseCommit")
    if (document.get("schema") != INDEX_SCHEMA or document.get("version") != version
            or not isinstance(commit, str) or not _COMMIT.fullmatch(commit)):
        raise UpdateCheckError(f"The update index does not describe release {version}.")
    prefix = f"MonkeyHub-{version}-windows-x64-candidate.zip"
    _entry(document.get("releaseManifest"), prefix + ".release-manifest.json", "release manifest")
    patches = document.get("patches")
    if not isinstance(patches, list):
        raise UpdateCheckError("The update index lists no patches.")
    for row in patches:
        base = row.get("baseVersion") if isinstance(row, dict) else None
        if _version(base) is None or not isinstance(row.get("baseCommit"), str) or not _COMMIT.fullmatch(row["baseCommit"]):
            raise UpdateCheckError("The update index lists a patch without a base release.")
        _entry(row, f"MonkeyHub-{version}-from-{base}.patch.zip", "patch")
    return document


def _release_manifest(data: bytes, version: str, commit: str) -> dict:
    document = _json_object(data, "The release manifest")
    release = document.get("release") if isinstance(document.get("release"), dict) else {}
    build_info = document.get("buildInfo") if isinstance(document.get("buildInfo"), dict) else {}
    if (document.get("schema") != "ReleaseManifest@1" or release.get("version") != version
            or release.get("sourceCommit") != commit or release.get("target") != "windows-x64"
            or not isinstance(build_info.get("sha256"), str) or not _SHA256.fullmatch(build_info["sha256"])):
        raise UpdateCheckError(f"The release manifest does not describe release {version} at {commit[:12]}.")
    return document
