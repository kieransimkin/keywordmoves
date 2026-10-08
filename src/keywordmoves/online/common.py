"""Bounded, optional HTTP access and explicit evidence for online sources.

Nothing in this module imports a network dependency or reads credentials at import time.
"""
from __future__ import annotations

import json
import math
import re
import time
from datetime import date, datetime, timezone
from typing import Any, Mapping
from urllib.parse import urlsplit

from ..credentials import credential_value
from ..errors import ConfigurationError, KeywordMovesError
from ..models import (
    ExecutionContext,
    KeywordCandidate,
    KeywordEvidence,
    PluginRequest,
    PluginResult,
)

USER_AGENT = "KeywordMoves/0.2 (+https://github.com/kieransimkin/keywordmoves)"


class OnlineSourceError(KeywordMovesError):
    """A request failed or its response cannot be interpreted reliably."""


def text(options: Mapping[str, Any], key: str, default: Any = None) -> str:
    value = options.get(key, default)
    if not isinstance(value, (str, int)) or isinstance(value, bool) or not str(value).strip():
        raise ConfigurationError(f"Supply a non-empty {key} option.")
    result = str(value).strip()
    if any(ord(c) < 32 for c in result):
        raise ConfigurationError(f"Control characters are not allowed in {key}.")
    return result


def integer(options: Mapping[str, Any], key: str, default: int, low: int, high: int) -> int:
    value = options.get(key, default)
    if isinstance(value, bool) or not re.fullmatch(r"[0-9]+", str(value)):
        raise ConfigurationError(f"{key} must be an integer between {low} and {high}.")
    value = int(value)
    if not low <= value <= high:
        raise ConfigurationError(f"{key} must be between {low} and {high}.")
    return value


def boolean(options: Mapping[str, Any], key: str, default: bool = False) -> bool:
    value = options.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.lower() in {"true", "false"}:
        return value.lower() == "true"
    raise ConfigurationError(f"{key} must be true or false.")


def choice(options: Mapping[str, Any], key: str, default: str, allowed: tuple[str, ...]) -> str:
    value = text(options, key, default)
    if value not in allowed:
        raise ConfigurationError(f"{key} must be one of: {', '.join(allowed)}.")
    return value


def code(options: Mapping[str, Any], key: str, default: str | None = None,
         pattern: str = r"[A-Za-z]{2}") -> str:
    value = text(options, key, default)
    if not re.fullmatch(pattern, value):
        raise ConfigurationError(f"{key} has an invalid format; see docs/online-sources.md.")
    return value


def iso_date(options: Mapping[str, Any], key: str) -> str:
    value = text(options, key)
    try:
        parsed = date.fromisoformat(value)
        if value != parsed.isoformat():
            raise ValueError
    except ValueError:
        raise ConfigurationError(f"{key} must use YYYY-MM-DD.") from None
    return value


def secret(options: Mapping[str, Any], key: str, env: str) -> str:
    value = options[key] if key in options else credential_value(env)
    if not isinstance(value, str) or not value.strip() or any(ord(c) < 32 for c in value):
        raise ConfigurationError(f"Set {env} or --option {key}=... to a valid credential.")
    return value.strip()


def seeds(request: PluginRequest, maximum: int = 1) -> list[str]:
    values = list(dict.fromkeys(request.keywords))
    if not 1 <= len(values) <= maximum:
        raise ConfigurationError(f"Supply between 1 and {maximum} --keyword values.")
    if any(not isinstance(v, str) or not v.strip() or len(v) > 400 or
           any(ord(c) < 32 for c in v) for v in values):
        raise ConfigurationError("Keywords must be non-empty text, at most 400 characters each.")
    return [v.strip() for v in values]


def number(value: Any) -> int | float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise OnlineSourceError("The provider returned a boolean instead of a numeric metric.")
    try:
        result = float(value)
    except (ValueError, TypeError, OverflowError):
        raise OnlineSourceError("The provider returned an invalid numeric metric.") from None
    if not math.isfinite(result):
        raise OnlineSourceError("The provider returned a non-finite numeric metric.")
    return int(result) if result.is_integer() else result


def obj(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise OnlineSourceError("Unexpected provider response: expected a JSON object.")
    return value


def array(value: Any) -> list[Any]:
    if not isinstance(value, list):
        raise OnlineSourceError("Unexpected provider response: expected a result array.")
    return value


def phrase(value: Any) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 4000:
        raise OnlineSourceError("Unexpected provider response: invalid keyword text.")
    return " ".join(value.split())


def provider_error(payload: Any) -> dict[str, Any]:
    payload = obj(payload)
    if payload.get("error") or payload.get("errors"):
        raise OnlineSourceError("The provider reported an API error; check access, quota and options.")
    return payload


def candidate(source: str, word: Any, metrics: Mapping[str, tuple[Any, str]], *,
              observed_at: str, geography: str | None = None, relationship: str = "discovered",
              metadata: Mapping[str, Any] | None = None, note: str = "") -> KeywordCandidate:
    return KeywordCandidate(
        phrase=phrase(word), relationship=relationship, score=None,
        evidence=tuple(KeywordEvidence(source=source, metric=metric, value=value, unit=unit,
                                       observed_at=observed_at, geography=geography, notes=note)
                       for metric, (value, unit) in metrics.items()),
        metadata=dict(metadata or {}),
    )


class HTTP:
    """One run, fixed hosts, no redirects/retries, bounded decoded response size.

    transport is an optional httpx transport for tests. It is never set via CLI options.
    """
    def __init__(self, options: Mapping[str, Any], hosts: tuple[str, ...], transport: Any = None,
                 interval: float = 1.0) -> None:
        self.maximum = integer(options, "max_requests", 5, 1, 20)
        self.max_bytes = integer(options, "max_response_bytes", 2_000_000, 1024, 10_000_000)
        self.timeout = integer(options, "timeout", 30, 1, 120)
        self.interval = max(interval, integer(options, "min_interval", 1, 0, 60))
        self.hosts, self.transport = hosts, transport
        self.requests = 0
        self.last_request = 0.0
        self.client: Any = None

    def __enter__(self) -> HTTP:
        try:
            import httpx
        except ImportError:
            raise ConfigurationError("Install the optional dependencies: pip install 'keywordmoves[online]'.") from None
        self.client = httpx.Client(transport=self.transport, timeout=self.timeout, trust_env=False,
                                   follow_redirects=False, headers={"User-Agent": USER_AGENT})
        return self

    def __exit__(self, *args: Any) -> None:
        self.client.close()

    def request(self, method: str, url: str, *, allowed_statuses: tuple[int, ...] = (),
                **kwargs: Any) -> tuple[int, str]:
        import httpx

        try:
            parsed = urlsplit(url)
            port = parsed.port
        except ValueError:
            raise ConfigurationError("The provider URL is malformed.") from None
        if (parsed.scheme != "https" or parsed.hostname not in self.hosts or parsed.username
                or parsed.password or port not in (None, 443) or parsed.fragment):
            raise ConfigurationError("Network requests must use an approved HTTPS provider host.")
        if self.requests >= self.maximum:
            raise OnlineSourceError("The request budget was exhausted; no additional requests were made.")
        delay = self.interval - (time.monotonic() - self.last_request)
        if delay > 0:
            time.sleep(delay)
        self.requests += 1
        self.last_request = time.monotonic()
        try:
            with self.client.stream(method, url, **kwargs) as response:
                status = response.status_code
                if status not in allowed_statuses and not 200 <= status < 300:
                    detail = {
                        401: "Authentication failed.", 402: "Access or credits are required.",
                        403: "Access was denied; no bypass was attempted.",
                        429: "Rate limited; stop and retry later according to the provider's limits.",
                    }.get(status, "Request failed; redirects and automatic retries are disabled.")
                    raise OnlineSourceError(f"Provider HTTP {status}. {detail}")
                parts: list[bytes] = []
                length = 0
                for block in response.iter_bytes():
                    length += len(block)
                    if length > self.max_bytes:
                        raise OnlineSourceError("The provider response exceeded max_response_bytes.")
                    parts.append(block)
                return status, b"".join(parts).decode("utf-8-sig")
        except (httpx.HTTPError, UnicodeDecodeError, ValueError):
            raise OnlineSourceError("Network, TLS, timeout or response-decoding failure; no retry was made.") from None

    def json(self, method: str, url: str, **kwargs: Any) -> Any:
        _, value = self.request(method, url, **kwargs)
        try:
            return json.loads(value, parse_constant=self._invalid_constant)
        except (ValueError, TypeError, RecursionError):
            raise OnlineSourceError("Expected JSON, but received an unreadable response or website challenge.") from None

    @staticmethod
    def _invalid_constant(value: str) -> None:
        raise ValueError("Invalid JSON constant")


class OnlinePlugin:
    hosts: tuple[str, ...] = ()
    access = "official-api"
    interval = 1.0

    def __init__(self, *, transport: Any = None) -> None:
        self._transport = transport

    def run(self, request: PluginRequest, context: ExecutionContext) -> PluginResult:
        del context
        if request.operation not in self.descriptor.operations:
            raise ConfigurationError(f"{self.descriptor.name} operations: {', '.join(self.descriptor.operations)}.")
        if request.inputs:
            raise ConfigurationError("This network plugin accepts --keyword and options, not input files.")
        if any(key == "llm" or key.startswith("llm_") for key in request.options):
            raise ConfigurationError("Online source plugins do not use --llm, --model or llm_ options.")
        limit = integer(request.options, "limit", 50, 1, 1000)
        timestamp = datetime.now(timezone.utc).isoformat()
        with HTTP(request.options, self.hosts_for(request), self._transport, self.interval) as http:
            items, metadata, notes = self.fetch(request, http, timestamp[:10])
            count = http.requests
        # Deliberately retain provider ordering and scope. Do not turn scores into demand.
        metadata = {**metadata, "access": self.access, "retrieved_at": timestamp,
                    "request_count": count, "rows_received": len(items),
                    "output_truncated": len(items) > limit,
                    "completeness": metadata.get("completeness", "provider-limited; not exhaustive")}
        return PluginResult(self.descriptor.name, request.operation, tuple(items[:limit]),
                            tuple(notes) + ("Suggestions are not search volume; unavailable data is not zero demand.",),
                            metadata)

    def hosts_for(self, request: PluginRequest) -> tuple[str, ...]:
        return self.hosts

    def fetch(self, request: PluginRequest, http: HTTP, observed: str) -> tuple[list[KeywordCandidate], dict, list[str]]:
        raise NotImplementedError
