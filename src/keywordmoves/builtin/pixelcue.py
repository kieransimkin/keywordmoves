from __future__ import annotations

import json
import math
import uuid
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from ..errors import ConfigurationError, InputError
from ..models import (
    ExecutionContext,
    KeywordCandidate,
    KeywordEvidence,
    PluginDescriptor,
    PluginRequest,
    PluginResult,
)

DEFAULT_ENDPOINT = "http://127.0.0.1:8765"
DEFAULT_TIMEOUT = 21_600
MAX_RESPONSE_BYTES = 1024 * 1024


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


# The payload contains a local path. Keep the request on the selected service.
_LOCAL_HTTP = build_opener(ProxyHandler({}), _NoRedirects())


def _endpoint(value: object) -> str:
    endpoint = str(value).strip().rstrip("/")
    try:
        url = urlsplit(endpoint)
        port = url.port
    except ValueError as exc:
        raise ConfigurationError("PixelCue endpoint must be a valid loopback HTTP(S) origin.") from exc
    if url.username is not None or url.password is not None:
        raise ConfigurationError("PixelCue endpoint must not contain credentials.")
    if (
        url.scheme not in {"http", "https"}
        or url.hostname not in {"127.0.0.1", "::1", "localhost"}
        or any(char.isspace() for char in endpoint)
        or url.path or url.query or url.fragment or port == 0
    ):
        raise ConfigurationError("PixelCue endpoint must be a loopback HTTP(S) origin.")
    return endpoint


def _timeout(value: object) -> float:
    try:
        timeout = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ConfigurationError("PixelCue timeout must be a positive finite number.") from exc
    if isinstance(value, bool) or not math.isfinite(timeout) or timeout <= 0:
        raise ConfigurationError("PixelCue timeout must be a positive finite number.")
    return timeout


def _limit(value: object) -> int | None:
    if value is None:
        return None
    try:
        limit = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ConfigurationError("PixelCue limit must be a positive integer.") from exc
    if isinstance(value, bool) or limit <= 0 or (not isinstance(value, str) and value != limit):
        raise ConfigurationError("PixelCue limit must be a positive integer.")
    return limit


class PixelCueKeywordPlugin:
    descriptor = PluginDescriptor(
        name="pixelcue",
        summary="Extract visual keyword proposals from one file through a local PixelCue service.",
        capabilities=("generate", "extract", "visual-media", "local-service"),
        operations=("extract",),
    )

    def run(self, request: PluginRequest, context: ExecutionContext) -> PluginResult:
        if request.operation != "extract":
            raise ConfigurationError("PixelCue supports the extract operation.")
        if len(request.inputs) != 1 or request.keywords:
            raise InputError("PixelCue needs exactly one --input file and no --keyword.")
        source = request.inputs[0].expanduser().resolve()
        if not source.is_file():
            raise InputError("PixelCue input must be an existing file.")

        endpoint = _endpoint(request.options.get("endpoint", DEFAULT_ENDPOINT))
        timeout = _timeout(request.options.get("timeout", DEFAULT_TIMEOUT))
        limit = _limit(request.options.get("limit"))
        model = str(request.options.get("model") or "").strip()
        request_id = str(request.options.get("request_id") or "").strip() or str(uuid.uuid4())
        payload = {"path": str(source), "request_id": request_id}
        if model:
            payload["model"] = model
        http_request = Request(
            f"{endpoint}/v1/keywords",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with _LOCAL_HTTP.open(http_request, timeout=timeout) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except HTTPError as exc:
            exc.close()
            raise InputError(f"PixelCue returned HTTP {exc.code}.") from exc
        except (URLError, OSError) as exc:
            raise InputError(f"Could not read the local PixelCue service at {endpoint}: {exc}") from exc
        if len(raw) > MAX_RESPONSE_BYTES:
            raise InputError("PixelCue response exceeds 1 MiB.")
        try:
            tags = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise InputError("PixelCue returned malformed UTF-8 JSON.") from exc
        if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
            raise InputError("PixelCue response must be a JSON list of strings.")

        unique: dict[str, str] = {}
        for tag in tags:
            phrase = tag.strip()
            if phrase:
                unique.setdefault(phrase.casefold(), phrase)
        evidence = KeywordEvidence(
            source="pixelcue",
            metric="vlm_generated_tag",
            value=1,
            unit="generated tag",
            notes="Visual-model proposal for the supplied file. Search demand is measured separately.",
        )
        return PluginResult(
            plugin=self.descriptor.name,
            operation=request.operation,
            keywords=tuple(
                KeywordCandidate(phrase, relationship="pixelcue-visual-tag", evidence=(evidence,))
                for phrase in list(unique.values())[:limit]
            ),
            notes=("Review visual proposals before using them as keywords.",),
            metadata={
                "source_file": str(source),
                "endpoint": endpoint,
                "model": model or "server-default",
                "request_id": request_id,
            },
        )
