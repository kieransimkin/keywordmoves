"""Verify only explicitly permitted local capture references; never fetch a URL."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any


def verify_captures(rows: list[dict[str, Any]], root: Path, *,
                    max_files: int = 5000) -> dict[str, Any]:
    resolved_root = root.resolve(strict=True)
    references: dict[tuple[str, str | None], int] = {}
    for row in rows:
        if row.get("capture_file"):
            key = (row["capture_file"], row.get("capture_sha256"))
            references[key] = references.get(key, 0) + 1
    checked = []
    for (filename, expected), count in list(references.items())[:max_files]:
        candidate = Path(filename)
        if not candidate.is_absolute():
            candidate = resolved_root / candidate
        item = {"capture": filename, "expected_sha256": expected, "reference_count": count}
        try:
            resolved = candidate.resolve(strict=True)
            if not resolved.is_relative_to(resolved_root):
                item["status"] = "outside-permitted-root"
            elif not expected:
                item["status"] = "missing-expected-hash"
            elif not resolved.is_file() or resolved.stat().st_size > 20_000_000:
                item["status"] = "not-a-bounded-file"
            else:
                hasher = hashlib.sha256()
                size = 0
                with resolved.open("rb") as handle:
                    while block := handle.read(65536):
                        size += len(block)
                        if size > 20_000_000:
                            raise OSError("Capture grew beyond its bound.")
                        hasher.update(block)
                item["actual_sha256"] = hasher.hexdigest()
                item["status"] = ("verified" if item["actual_sha256"].casefold() == expected.casefold()
                                  else "hash-mismatch")
        except (OSError, RuntimeError):
            item["status"] = "unavailable"
        checked.append(item)
    counts: dict[str, int] = {}
    for item in checked:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    return {
        "permitted_root": str(resolved_root), "distinct_references": len(references),
        "status_counts": counts, "rows": checked,
        "output_truncated": len(references) > max_files,
        "all_references_verified": bool(references) and len(references) <= max_files
                                   and all(item["status"] == "verified" for item in checked),
        "note": "Byte identity is checked; this does not attest to capture accuracy or access rights.",
    }
