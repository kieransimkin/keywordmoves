"""Explicit, optional OS credential storage; no secret reads at import time."""
from __future__ import annotations

import getpass
import importlib
import os
import re
import sys
import warnings
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Iterator, Sequence

from .errors import ConfigurationError

DEFAULT_SERVICE = "KeywordMoves"
KNOWN_CREDENTIALS = (
    "AHREFS_API_KEY", "ALSOASKED_API_KEY", "APIFY_TOKEN", "BING_WEBMASTER_ACCESS_TOKEN",
    "BING_WEBMASTER_API_KEY", "BRAVE_SEARCH_API_KEY", "DATAFORSEO_LOGIN",
    "DATAFORSEO_PASSWORD", "GOOGLE_ADS_ACCESS_TOKEN", "GOOGLE_ADS_DEVELOPER_TOKEN",
    "GOOGLE_CUSTOM_SEARCH_API_KEY", "INSTAGRAM_ACCESS_TOKEN", "KEYWORDS_EVERYWHERE_API_KEY",
    "KEYWORDTOOL_API_KEY", "OPENAI_API_KEY", "PAGESPEED_API_KEY", "REDDIT_ACCESS_TOKEN",
    "SEARCH_CONSOLE_ACCESS_TOKEN", "SEMRUSH_API_KEY", "SERPAPI_API_KEY", "TIKTOK_ACCESS_TOKEN",
    "TIKTOK_COMMERCIAL_ACCESS_TOKEN", "TIKTOK_RESEARCH_ACCESS_TOKEN", "YOUTUBE_ACCESS_TOKEN",
    "YOUTUBE_API_KEY",
)
_OS_BACKENDS = {
    ("keyring.backends.Windows", "WinVaultKeyring"),
    ("keyring.backends.macOS", "Keyring"),
    ("keyring.backends.SecretService", "Keyring"),
    ("keyring.backends.kwallet", "DBusKeyring"),
    ("keyring.backends.kwallet", "DBusKeyringKWallet4"),
}


@dataclass(frozen=True)
class CredentialSettings:
    store: str = "environment"
    service: str = DEFAULT_SERVICE


_settings: ContextVar[CredentialSettings | None] = ContextVar("credential_settings", default=None)


def _validate_service(service: str) -> str:
    if (not isinstance(service, str) or not service.strip() or len(service) > 200
            or any(ord(c) < 32 or ord(c) == 127 for c in service)):
        raise ConfigurationError("Credential service must be non-empty text, at most 200 characters.")
    return service.strip()


def _validate_name(name: str) -> str:
    if not isinstance(name, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", name):
        raise ConfigurationError("Credential names must use uppercase letters, numbers and underscores.")
    return name


def _validate_value(value: str) -> str:
    if (not isinstance(value, str) or not value.strip() or len(value) > 65536
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise ConfigurationError("Supply a non-empty, single-line credential of at most 65536 characters.")
    return value.strip()


def credential_settings() -> CredentialSettings:
    current = _settings.get()
    if current is not None:
        return current
    return _validated_settings(
        os.environ.get("KEYWORDMOVES_CREDENTIAL_STORE", "environment"),
        os.environ.get("KEYWORDMOVES_CREDENTIAL_SERVICE", DEFAULT_SERVICE),
    )


def _validated_settings(store: str, service: str) -> CredentialSettings:
    if store not in {"environment", "os-keyring"}:
        raise ConfigurationError("Credential store must be environment or os-keyring.")
    return CredentialSettings(store, _validate_service(service))


@contextmanager
def credential_context(*, store: str | None = None, service: str | None = None
                       ) -> Iterator[CredentialSettings]:
    """Select a store/profile within this call context, without changing process environment."""
    current = _settings.get()
    selected = _validated_settings(
        store if store is not None else (current.store if current else
                                        os.environ.get("KEYWORDMOVES_CREDENTIAL_STORE", "environment")),
        service if service is not None else (current.service if current else
                                            os.environ.get("KEYWORDMOVES_CREDENTIAL_SERVICE", DEFAULT_SERVICE)),
    )
    token = _settings.set(selected)
    try:
        yield selected
    finally:
        _settings.reset(token)


def _identity(backend: object) -> tuple[str, str]:
    return type(backend).__module__, type(backend).__name__


def _os_backend() -> object:
    try:
        keyring = importlib.import_module("keyring")
    except ImportError:
        raise ConfigurationError(
            "OS credential storage needs the optional dependency: "
            "pip install 'keywordmoves[credentials]'. Environment and CLI credentials still work."
        ) from None
    try:
        backend = keyring.get_keyring()
        if _identity(backend) == ("keyring.backends.chainer", "ChainerBackend"):
            # Use one supported OS backend directly; never delegate to a plaintext fallback.
            backend = next((item for item in backend.backends if _identity(item) in _OS_BACKENDS),
                           None)
        if _identity(backend) not in _OS_BACKENDS:
            raise ConfigurationError(
                "No supported OS credential store is configured. Use Windows Credential Manager, "
                "macOS Keychain, Linux Secret Service/KWallet, or environment/CLI credentials."
            )
        return backend
    except ConfigurationError:
        raise
    except Exception:
        raise ConfigurationError(
            "The OS credential store is unavailable. Unlock/configure it, or use environment/CLI credentials."
        ) from None


class OSCredentialStore:
    """Single OS-backed service/profile. No plaintext fallback and no network requests."""

    def __init__(self, service: str = DEFAULT_SERVICE) -> None:
        self.service = _validate_service(service)
        self._backend = _os_backend()

    @property
    def backend_name(self) -> str:
        module, name = _identity(self._backend)
        return module + "." + name

    def get(self, name: str) -> str | None:
        name = _validate_name(name)
        try:
            value = self._backend.get_password(self.service, name)
        except Exception:
            raise ConfigurationError(
                "Could not read the OS credential store. Unlock/configure it or use environment/CLI credentials."
            ) from None
        return None if value is None else _validate_value(value)

    def set(self, name: str, value: str) -> None:
        name, value = _validate_name(name), _validate_value(value)
        try:
            self._backend.set_password(self.service, name, value)
            persisted = self._backend.get_password(self.service, name)
        except Exception:
            raise ConfigurationError(
                "Could not store the credential. Check OS access and size limits; no file fallback was used."
            ) from None
        if persisted != value:
            raise ConfigurationError("The OS credential store did not confirm the saved value.")

    def delete(self, name: str) -> None:
        name = _validate_name(name)
        if self.get(name) is None:
            return
        try:
            self._backend.delete_password(self.service, name)
            persisted = self._backend.get_password(self.service, name)
        except Exception:
            raise ConfigurationError("Could not remove the credential from the OS store.") from None
        if persisted is not None:
            raise ConfigurationError("The OS credential store did not confirm removal.")


def credential_value(name: str) -> str | None:
    """Environment first, then explicitly selected OS keyring. An empty env value still wins."""
    name = _validate_name(name)
    if name in os.environ:
        return os.environ[name]
    settings = credential_settings()
    if settings.store == "os-keyring":
        return OSCredentialStore(settings.service).get(name)
    return None


def credential_status(names: Sequence[str] = KNOWN_CREDENTIALS) -> dict:
    settings = credential_settings()
    names = tuple(dict.fromkeys(_validate_name(name) for name in names))
    store = OSCredentialStore(settings.service) if settings.store == "os-keyring" else None
    return {
        "store": settings.store, "service": settings.service,
        "backend": store.backend_name if store else None,
        "credentials": {
            name: {"environment_present": name in os.environ,
                   "stored_present": store.get(name) is not None if store else None}
            for name in names
        },
        "network_requests": 0,
        "note": "Presence does not verify a credential, permission, account or API entitlement.",
    }


def read_credential(*, from_stdin: bool = False) -> str:
    """Read a credential without a literal command argument or echoing it."""
    try:
        if from_stdin:
            value = sys.stdin.read(65539)
            if value.endswith("\r\n"):
                value = value[:-2]
            elif value.endswith("\n"):
                value = value[:-1]
            return _validate_value(value)
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            return _validate_value(getpass.getpass("Credential (input hidden): "))
    except (EOFError, KeyboardInterrupt, getpass.GetPassWarning):
        raise ConfigurationError("Hidden credential input is unavailable; use --stdin with a secure pipe.") from None
