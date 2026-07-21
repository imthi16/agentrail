"""Shared pytest fixtures."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture()
def project_root(tmp_path: Path) -> Iterator[Path]:
    """A throwaway project directory for file-based state tests."""

    yield tmp_path
