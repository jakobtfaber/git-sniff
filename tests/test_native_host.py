import io
import json
import struct
import asyncio
import pytest

import git_sniff.native_host as nh
from git_sniff.schemas import RepoScorecard, PillarScores, RepoNotFoundError


def _scorecard():
    return RepoScorecard(
        repository="acme/widget",
        overall_score=88,
        status="HEALTHY",
        breakdown=PillarScores(maintenance=90, cicd=100, dependencies=75, bus_factor=85),
        recommendation="Production ready.",
        calculated_at="2026-05-26T00:00:00Z",
        rate_limit_warning=None,
    )


def test_framing_round_trip():
    msg = {"owner": "acme", "repo": "widget"}
    encoded = nh.encode_message(msg)
    assert struct.unpack("@I", encoded[:4])[0] == len(encoded) - 4
    decoded = nh.read_message(io.BytesIO(encoded))
    assert decoded == msg


def test_read_message_eof_returns_none():
    assert nh.read_message(io.BytesIO(b"")) is None


def test_read_message_multibyte_utf8():
    msg = {"owner": "ünïcödé", "repo": "✓"}
    decoded = nh.read_message(io.BytesIO(nh.encode_message(msg)))
    assert decoded == msg


def test_handle_success(monkeypatch):
    async def fake_evaluate(owner, repo, *, token=None, http_client=None):
        return _scorecard()
    monkeypatch.setattr(nh, "evaluate", fake_evaluate)
    monkeypatch.setattr(nh, "resolve_token", lambda: None)

    stdin = io.BytesIO(nh.encode_message({"owner": "acme", "repo": "widget"}))
    stdout = io.BytesIO()
    asyncio.run(nh._handle(stdin, stdout))

    stdout.seek(0)
    reply = nh.read_message(stdout)
    assert reply["overall_score"] == 88
    assert stdout.read() == b""


def test_handle_engine_error_becomes_error_field(monkeypatch):
    async def fake_evaluate(owner, repo, *, token=None, http_client=None):
        raise RepoNotFoundError("missing")
    monkeypatch.setattr(nh, "evaluate", fake_evaluate)
    monkeypatch.setattr(nh, "resolve_token", lambda: None)

    stdin = io.BytesIO(nh.encode_message({"owner": "acme", "repo": "missing"}))
    stdout = io.BytesIO()
    asyncio.run(nh._handle(stdin, stdout))
    stdout.seek(0)
    assert nh.read_message(stdout) == {"error": "missing"}


def test_handle_missing_fields(monkeypatch):
    monkeypatch.setattr(nh, "resolve_token", lambda: None)
    stdin = io.BytesIO(nh.encode_message({"owner": "acme"}))
    stdout = io.BytesIO()
    asyncio.run(nh._handle(stdin, stdout))
    stdout.seek(0)
    assert "error" in nh.read_message(stdout)


def test_handle_timeout(monkeypatch):
    async def slow_evaluate(owner, repo, *, token=None, http_client=None):
        await asyncio.sleep(0.2)
        return _scorecard()
    monkeypatch.setattr(nh, "evaluate", slow_evaluate)
    monkeypatch.setattr(nh, "resolve_token", lambda: None)
    monkeypatch.setattr(nh, "HOST_TIMEOUT", 0.01)

    stdin = io.BytesIO(nh.encode_message({"owner": "acme", "repo": "widget"}))
    stdout = io.BytesIO()
    asyncio.run(nh._handle(stdin, stdout))
    stdout.seek(0)
    reply = nh.read_message(stdout)
    assert "timed out" in reply["error"].lower()
