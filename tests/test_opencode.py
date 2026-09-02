"""OpenCode Go/Zen adapter: offline via httpx.MockTransport."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from httpx import Client, MockTransport, Request, Response

from agentrail.adapters.opencode import OpencodeApiAdapter
from agentrail.config import OpencodeSettings
from agentrail.models import Mode


def _fake_response(request: Request) -> Response:
    return Response(
        200,
        json={"choices": [{"message": {"content": "applied the change"}}]},
    )


def _adapter(settings: OpencodeSettings | None = None) -> OpencodeApiAdapter:
    return OpencodeApiAdapter(
        settings=settings or OpencodeSettings(),
        client_factory=lambda **kw: Client(transport=MockTransport(_fake_response), **kw),
    )


def test_registered_and_argv_echo(tmp_path: Path) -> None:
    a = OpencodeApiAdapter()
    argv = a.build_argv("do the thing", mode=Mode.ACCEPT_EDITS, model_id="glm-5.3")
    assert argv[0].endswith("/chat/completions")
    assert argv[1] == "model=glm-5.3"
    assert a.capabilities().non_interactive


def test_unavailable_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENCODE_API_KEY", raising=False)
    assert not OpencodeApiAdapter().available()


def test_start_without_key_marks_not_started(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OPENCODE_API_KEY", raising=False)
    handle = OpencodeApiAdapter().start(
        "prompt", mode=Mode.ACCEPT_EDITS, model_id="glm-5.3", cwd=tmp_path
    )
    assert not handle.started
    assert "OPENCODE_API_KEY" in handle.output


def test_dry_run_returns_without_http(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENCODE_API_KEY", "sk-test")
    handle = _adapter().start(
        "prompt", mode=Mode.ACCEPT_EDITS, model_id="glm-5.3", cwd=tmp_path, dry_run=True
    )
    assert not handle.started
    assert handle.output == "dry-run"


def test_start_posts_model_and_returns_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, str] = {}

    def handler(request: Request) -> Response:
        captured["path"] = request.url.path
        captured["auth"] = request.headers.get("authorization", "")
        captured["model"] = json.loads(request.content.decode())["model"]
        return _fake_response(request)

    monkeypatch.setenv("OPENCODE_API_KEY", "sk-test")
    adapter = OpencodeApiAdapter(
        client_factory=lambda **kw: Client(transport=MockTransport(handler), **kw)
    )
    handle = adapter.start("prompt", mode=Mode.ACCEPT_EDITS, model_id="kimi-k3", cwd=tmp_path)

    assert handle.started
    assert handle.output == "applied the change"
    assert captured["path"].endswith("/chat/completions")
    assert captured["auth"] == "Bearer sk-test"
    assert captured["model"] == "kimi-k3"


def test_start_missing_model_id_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENCODE_API_KEY", "sk-test")
    handle = _adapter().start("prompt", mode=Mode.ACCEPT_EDITS, model_id=None, cwd=tmp_path)
    assert not handle.started
    assert handle.output == "no routed model_id"


def test_zen_base_url_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    zen = OpencodeSettings(base_url="https://opencode.ai/zen/v1")
    adapter = OpencodeApiAdapter(settings=zen)
    assert adapter.base_url == "https://opencode.ai/zen/v1"
    argv = adapter.build_argv("x", mode=Mode.PLAN, model_id="kimi-k3")
    assert argv[0].startswith("POST https://opencode.ai/zen/v1")
