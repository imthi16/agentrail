"""Shared pytest fixtures."""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture()
def project_root(tmp_path: Path) -> Iterator[Path]:
    """A throwaway project directory for file-based state tests."""

    yield tmp_path


def _run(args: list[str], cwd: Path) -> None:
    subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture()
def temp_git_repo(tmp_path: Path) -> Iterator[Path]:
    """A real initialized git repo with one commit on ``main``.

    Skips if git is unavailable in this environment.
    """

    if shutil.which("git") is None:
        pytest.skip("git not installed")

    _run(["git", "init", "-b", "main"], tmp_path)
    _run(["git", "config", "user.email", "test@agentrail.dev"], tmp_path)
    _run(["git", "config", "user.name", "AgentRail Test"], tmp_path)
    (tmp_path / "README.md").write_text("# temp repo\n", encoding="utf-8")
    _run(["git", "add", "."], tmp_path)
    _run(["git", "commit", "-m", "initial"], tmp_path)
    yield tmp_path
