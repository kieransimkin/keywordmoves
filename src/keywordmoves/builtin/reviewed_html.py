"""Small shared primitives for explicit, offline public-tool captures."""
from __future__ import annotations

import hashlib
from pathlib import Path

from ..errors import ConfigurationError, InputError
from ..monitoring.validation import now_stamp, stamp
from ..online.youtube_imports import soup


def visible_document(request, url, options, *, captured_bytes=None):
    if (len(request.inputs) != (1 if captured_bytes is None else 0)
        or set(request.options) - options):
        raise ConfigurationError("Use one reviewed HTML file and the documented offline options.")
    if request.options.get("source_url") != url:
        raise ConfigurationError("Confirm the exact official source_url for this capture.")
    observed = stamp(request.options.get("observed_at"))
    if observed > now_stamp():
        raise InputError("The capture date cannot be in the future.")
    path = Path(request.inputs[0]) if captured_bytes is None else None
    try:
        if path is not None:
            with path.open("rb") as handle:
                raw = handle.read(5_000_001)
        else:
            raw = captured_bytes
        if not isinstance(raw, bytes):
            raise InputError("Automatic captures must supply UTF-8 bytes.")
        if len(raw) > 5_000_000:
            raise InputError("Reviewed HTML exceeds the 5 MB bound.")
        document = soup(raw.decode("utf-8-sig"))
    except (OSError, UnicodeError) as exc:
        raise InputError("Reviewed HTML must be readable UTF-8.") from exc
    # Do not recover stale results from scripts, templates or explicitly hidden UI.
    for node in list(document.find_all(["script", "style", "template", "noscript"])):
        node.decompose()
    for node in list(document.find_all(True)):
        if node.attrs is None:
            continue
        style = "".join(str(node.get("style", "")).lower().split())
        if (node.has_attr("hidden") or node.get("aria-hidden") == "true"
            or "hidden" in node.get("class", [])
            or "display:none" in style or "visibility:hidden" in style):
            node.decompose()
    return document, observed, {
        "input_path": str(path.resolve()) if path else None, "input_sha256": hashlib.sha256(raw).hexdigest(),
        "source_url": url, "observed_at": observed, "network_requests": 0,
        "access": "reviewed public HTML capture", "provider_updated_at": None,
        "freshness": "provider measurement date unknown",
    }


def one(root, selector):
    found = root.select(selector)
    if len(found) != 1:
        raise InputError("Expected one labelled public result element; re-review the source layout.")
    return found[0]
