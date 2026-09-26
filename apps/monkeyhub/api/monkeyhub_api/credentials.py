"""Provider keys the architect enters in Hub settings (#334).

A key goes into the local account's Windows Credential Manager and is read back
only by the Hub, to hand to the process that uses it. HTTP sees a key's status
and where it comes from, never its value. A key the environment already names
still wins, as before, and the Settings page says which one is in use.
"""

from __future__ import annotations

import ctypes
import os
import sys
import threading
from dataclasses import dataclass
from typing import Literal, Mapping, Protocol

CredentialId = Literal["gemini", "coding-plan"]


@dataclass(frozen=True)
class CredentialSlot:
    id: CredentialId
    # The Credential Manager entry, one per slot, owned by this account.
    target: str
    # Variables that, when the Hub was launched with them, win over the saved key.
    environment: tuple[str, ...]


SLOTS: dict[str, CredentialSlot] = {
    "gemini": CredentialSlot("gemini", "MonkeyHub/gemini-api-key", ("MONKEYHUB_RENDER_API_KEY",)),
    # The Claude CLI's own endpoint configuration is read by the chat layer, which
    # knows where it lives; a key saved here is the Hub's own Coding Plan token.
    "coding-plan": CredentialSlot("coding-plan", "MonkeyHub/coding-plan-token", ()),
}
# Visible ASCII only: every provider key in scope is one, and anything else is a
# paste of the wrong thing (a sentence, a quoted value, a line break).
_MIN, _MAX = 8, 1024


class CredentialError(ValueError):
    """A key that cannot be saved, said without repeating it."""


class SecretStore(Protocol):
    available: bool

    def read(self, target: str) -> str | None: ...

    def write(self, target: str, value: str) -> None: ...

    def delete(self, target: str) -> None: ...


class UnavailableSecretStore:
    """Where no account credential store exists, nothing is saved and only the environment counts."""

    available = False

    def read(self, target: str) -> str | None:
        return None

    def write(self, target: str, value: str) -> None:
        raise CredentialError("This system has no account credential store; provide the key through the environment instead.")

    def delete(self, target: str) -> None:
        return None


class MemorySecretStore:
    """A per-process store for tests; never used by a running Hub."""

    available = True

    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def read(self, target: str) -> str | None:
        return self.values.get(target)

    def write(self, target: str, value: str) -> None:
        self.values[target] = value

    def delete(self, target: str) -> None:
        self.values.pop(target, None)


class WindowsCredentialStore:
    """Generic credentials of the signed-in account, kept on this machine (not roamed)."""

    available = True
    _TYPE_GENERIC = 1
    _PERSIST_LOCAL_MACHINE = 2
    _ERROR_NOT_FOUND = 1168

    def __init__(self) -> None:
        from ctypes import wintypes

        class Credential(ctypes.Structure):
            _fields_ = [
                ("Flags", wintypes.DWORD), ("Type", wintypes.DWORD), ("TargetName", wintypes.LPWSTR),
                ("Comment", wintypes.LPWSTR), ("LastWritten", wintypes.FILETIME),
                ("CredentialBlobSize", wintypes.DWORD), ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
                ("Persist", wintypes.DWORD), ("AttributeCount", wintypes.DWORD), ("Attributes", ctypes.c_void_p),
                ("TargetAlias", wintypes.LPWSTR), ("UserName", wintypes.LPWSTR),
            ]

        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        self._credential = Credential
        self._write = advapi32.CredWriteW
        self._write.argtypes = [ctypes.POINTER(Credential), wintypes.DWORD]
        self._write.restype = wintypes.BOOL
        self._read = advapi32.CredReadW
        self._read.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.POINTER(Credential))]
        self._read.restype = wintypes.BOOL
        self._delete = advapi32.CredDeleteW
        self._delete.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
        self._delete.restype = wintypes.BOOL
        self._free = advapi32.CredFree
        self._free.argtypes = [ctypes.c_void_p]
        self._free.restype = None

    def read(self, target: str) -> str | None:
        found = ctypes.POINTER(self._credential)()
        if not self._read(target, self._TYPE_GENERIC, 0, ctypes.byref(found)):
            code = ctypes.get_last_error()
            if code == self._ERROR_NOT_FOUND:
                return None
            raise OSError(code, "The account credential store could not be read.")
        try:
            size = found.contents.CredentialBlobSize
            return ctypes.string_at(found.contents.CredentialBlob, size).decode("utf-16-le") if size else None
        finally:
            self._free(found)

    def write(self, target: str, value: str) -> None:
        blob = value.encode("utf-16-le")
        buffer = (ctypes.c_ubyte * len(blob)).from_buffer_copy(blob)
        credential = self._credential()
        credential.Type = self._TYPE_GENERIC
        credential.TargetName = target
        credential.CredentialBlobSize = len(blob)
        credential.CredentialBlob = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))
        credential.Persist = self._PERSIST_LOCAL_MACHINE
        credential.UserName = "MonkeyHub"
        if not self._write(ctypes.byref(credential), 0):
            raise OSError(ctypes.get_last_error(), "The account credential store refused the key.")

    def delete(self, target: str) -> None:
        if not self._delete(target, self._TYPE_GENERIC, 0) and ctypes.get_last_error() != self._ERROR_NOT_FOUND:
            raise OSError(ctypes.get_last_error(), "The account credential store could not remove the key.")


_lock = threading.Lock()
# Empty until the Hub process opens the account's store, so nothing else that
# imports this module, a test least of all, ever reads the account's keys.
_store: SecretStore = UnavailableSecretStore()


def account_store() -> SecretStore:
    return WindowsCredentialStore() if sys.platform == "win32" else UnavailableSecretStore()


def secret_store() -> SecretStore:
    with _lock:
        return _store


def use_secret_store(store: SecretStore) -> None:
    global _store
    with _lock:
        _store = store


def checked(value: str) -> str:
    """The key as it will be saved, or a CredentialError that never repeats it."""
    key = value.strip()
    if not _MIN <= len(key) <= _MAX:
        raise CredentialError(f"A key is {_MIN} to {_MAX} characters long.")
    if any(not 0x21 <= ord(character) <= 0x7E for character in key) or key[0] in "\"'" or key[-1] in "\"'":
        raise CredentialError("A key contains only visible ASCII characters, with no spaces, quotes around it or line breaks.")
    return key


def saved(slot: str) -> str | None:
    try:
        return secret_store().read(SLOTS[slot].target)
    except OSError:
        return None


def save(slot: str, value: str) -> None:
    secret_store().write(SLOTS[slot].target, checked(value))


def clear(slot: str) -> None:
    secret_store().delete(SLOTS[slot].target)


def from_environment(slot: str, environment: Mapping[str, str] | None = None) -> str | None:
    values = os.environ if environment is None else environment
    for name in SLOTS[slot].environment:
        if (values.get(name) or "").strip():
            return name
    return None


def resolve(slot: str, environment: Mapping[str, str] | None = None) -> str | None:
    """The key a process is given: the environment's when it names one, else the saved key."""
    values = os.environ if environment is None else environment
    name = from_environment(slot, values)
    return values[name].strip() if name is not None else saved(slot)


def saved_values() -> list[str]:
    """Every saved key, so redaction can remove them wherever they might surface."""
    return [value for value in (saved(slot) for slot in SLOTS) if value]


# Where to get a key, by the id the Settings page asks for. The page can only
# name one of these; the Hub never opens an address a page supplies.
PROVIDER_LINKS: dict[str, str] = {
    "gemini-keys": "https://aistudio.google.com/apikey",
    # Each vendor's own Claude Code guide names these pages (checked 2026-09-26).
    "zhipu-keys": "https://bigmodel.cn/coding-plan/personal/overview",
    "moonshot-keys": "https://platform.kimi.com/console/api-keys",
    "deepseek-keys": "https://platform.deepseek.com/api_keys",
    "bailian-keys": "https://bailian.console.aliyun.com/cn-beijing/subscription/coding-plan",
}


def open_link(link_id: str) -> bool:
    import webbrowser

    return webbrowser.open(PROVIDER_LINKS[link_id])


_GEMINI_MODELS = "https://generativelanguage.googleapis.com/v1beta/models?pageSize=1"


def check_gemini(key: str | None, *, timeout: float = 10.0) -> dict[str, str]:
    """Ask Gemini to list one model with the key: no content is generated and nothing is billed.

    The answer says whether Google took the key; it never repeats the key or
    Google's response body."""
    from urllib.error import HTTPError, URLError
    from urllib.request import Request, urlopen

    if not key:
        return {"result": "missing", "detail": "No Gemini key is saved or named by the environment."}
    request = Request(_GEMINI_MODELS, headers={"x-goog-api-key": key})
    try:
        with urlopen(request, timeout=timeout) as response:
            response.read(1)
        return {"result": "accepted", "detail": "Google accepted this key."}
    except HTTPError as exc:
        if exc.code in (400, 401, 403):
            return {"result": "rejected", "detail": f"Google refused this key (HTTP {exc.code})."}
        return {"result": "unreachable", "detail": f"Google answered HTTP {exc.code}; try again later."}
    except (URLError, OSError):
        return {"result": "unreachable", "detail": "Google could not be reached from this computer; check the network or proxy."}
