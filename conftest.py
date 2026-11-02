"""Keep pytest fixtures off stale Windows temp roots and CR/LF-sensitive checkouts."""

from __future__ import annotations

import hashlib
import os
import shutil
import uuid
from collections.abc import Mapping
from pathlib import Path

import pytest


def default_temp_root(
    checkout: Path,
    *,
    environment: Mapping[str, str] | None = None,
    platform: str | None = None,
) -> Path:
    """Choose a per-checkout, user-owned location for pytest scratch data."""
    current_environment = os.environ if environment is None else environment
    platform = os.name if platform is None else platform
    checkout = Path(checkout).resolve()
    if platform == "nt":
        base = Path(current_environment.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        return base / checkout.name / "pytest-tmp"
    return checkout / ".pytest-tmp"


def _new_run_root(checkout: Path) -> Path:
    """Return a fresh root so a killed run can never poison the next run."""
    checkout = checkout.resolve()
    key = hashlib.sha256(str(checkout).casefold().encode("utf-8")).hexdigest()[:12]
    return default_temp_root(checkout) / (
        f"direct-{key}-{os.getpid()}-{uuid.uuid4().hex}"
    )


def _probe_temp_root(root: Path) -> None:
    """Fail before collection when create/read/atomic-rename/cleanup is unavailable."""
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    probe = root / f"probe-{uuid.uuid4().hex}"
    renamed = root / f"{probe.name}-renamed"
    try:
        probe.mkdir()
        marker = probe / "probe.txt"
        marker.write_text("pytest-temp-probe", encoding="utf-8")
        if marker.read_text(encoding="utf-8") != "pytest-temp-probe":
            raise OSError(f"pytest temp probe readback failed: {probe}")
        probe.rename(renamed)
    finally:
        shutil.rmtree(renamed, ignore_errors=True)
        shutil.rmtree(probe, ignore_errors=True)
    if probe.exists() or renamed.exists():
        raise OSError(f"pytest temp probe cleanup failed: {root}")


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config: pytest.Config) -> None:
    """Redirect pytest's default tmp_path fixtures unless the caller chose a root."""
    if config.getoption("basetemp") is not None or os.environ.get("PYTEST_DEBUG_TEMPROOT"):
        return
    root = _new_run_root(Path(config.rootpath))
    _probe_temp_root(root)
    os.environ["PYTEST_DEBUG_TEMPROOT"] = str(root)
    config._inicache["cache_dir"] = str(root / "cache")
    config.add_cleanup(lambda: shutil.rmtree(root, ignore_errors=True))
