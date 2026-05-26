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


def test_handle_malformed_json_becomes_error(monkeypatch):
    monkeypatch.setattr(nh, "resolve_token", lambda: None)
    payload = b"{not valid json"
    framed = struct.pack("@I", len(payload)) + payload
    stdin = io.BytesIO(framed)
    stdout = io.BytesIO()
    asyncio.run(nh._handle(stdin, stdout))
    stdout.seek(0)
    reply = nh.read_message(stdout)
    assert "error" in reply


def test_handle_non_dict_payload_becomes_error(monkeypatch):
    monkeypatch.setattr(nh, "resolve_token", lambda: None)
    stdin = io.BytesIO(nh.encode_message(["not", "a", "dict"]))
    stdout = io.BytesIO()
    asyncio.run(nh._handle(stdin, stdout))
    stdout.seek(0)
    assert "error" in nh.read_message(stdout)


def test_read_message_rejects_oversize_length():
    framed = struct.pack("@I", nh.MAX_MESSAGE_BYTES + 1)
    with pytest.raises(ValueError):
        nh.read_message(io.BytesIO(framed))


import os
from pathlib import Path


def test_build_manifest_shape():
    m = nh.build_manifest("/abs/path/git-sniff-host")
    assert m["name"] == nh.HOST_NAME
    assert m["type"] == "stdio"
    assert m["path"] == "/abs/path/git-sniff-host"
    assert m["allowed_origins"] == [f"chrome-extension://{nh.EXTENSION_ID}/"]
    assert set(m) == {"name", "description", "path", "type", "allowed_origins"}


def test_install_writes_manifest(monkeypatch, tmp_path):
    monkeypatch.setattr(nh, "CHROME_NM_DIR", tmp_path)
    monkeypatch.setattr(nh, "host_binary_path", lambda: "/abs/bin/git-sniff-host")
    nh.install()
    target = tmp_path / f"{nh.HOST_NAME}.json"
    assert target.exists()
    written = json.loads(target.read_text())
    assert written["path"] == "/abs/bin/git-sniff-host"
    assert written["allowed_origins"] == [f"chrome-extension://{nh.EXTENSION_ID}/"]
    # idempotent + no temp file left behind
    nh.install()
    assert list(tmp_path.glob("*.tmp")) == []


def test_uninstall_removes_manifest(monkeypatch, tmp_path):
    monkeypatch.setattr(nh, "CHROME_NM_DIR", tmp_path)
    monkeypatch.setattr(nh, "host_binary_path", lambda: "/abs/bin/git-sniff-host")
    nh.install()
    nh.uninstall()
    assert not (tmp_path / f"{nh.HOST_NAME}.json").exists()
    nh.uninstall()  # idempotent, no error


def test_status_reports_origin_drift(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(nh, "CHROME_NM_DIR", tmp_path)
    monkeypatch.setattr(nh, "host_binary_path", lambda: "/abs/bin/git-sniff-host")
    target = tmp_path / f"{nh.HOST_NAME}.json"
    target.write_text(json.dumps({
        "name": nh.HOST_NAME, "description": "x", "path": "/abs/bin/git-sniff-host",
        "type": "stdio", "allowed_origins": ["chrome-extension://stalewrongidstalewrongidstale/"],
    }))
    nh.status()
    out = capsys.readouterr().out.lower()
    assert "drift" in out or "mismatch" in out


def test_install_cleans_tmp_on_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(nh, "CHROME_NM_DIR", tmp_path)
    monkeypatch.setattr(nh, "host_binary_path", lambda: "/abs/bin/git-sniff-host")

    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(nh.os, "replace", boom)
    with pytest.raises(OSError):
        nh.install()
    assert list(tmp_path.glob("*.tmp")) == []
    assert not (tmp_path / f"{nh.HOST_NAME}.json").exists()


def test_status_handles_malformed_manifest(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(nh, "CHROME_NM_DIR", tmp_path)
    monkeypatch.setattr(nh, "host_binary_path", lambda: "/abs/bin/git-sniff-host")
    (tmp_path / f"{nh.HOST_NAME}.json").write_text("{not json")
    nh.status()
    out = capsys.readouterr().out.lower()
    assert "invalid" in out


def test_status_reports_path_drift(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(nh, "CHROME_NM_DIR", tmp_path)
    monkeypatch.setattr(nh, "host_binary_path", lambda: "/new/bin/git-sniff-host")
    (tmp_path / f"{nh.HOST_NAME}.json").write_text(json.dumps({
        "name": nh.HOST_NAME, "description": "x", "path": "/old/bin/git-sniff-host",
        "type": "stdio", "allowed_origins": [f"chrome-extension://{nh.EXTENSION_ID}/"],
    }))
    nh.status()
    out = capsys.readouterr().out.lower()
    assert "path: drift" in out


def test_read_message_truncated_body_raises():
    framed = struct.pack("@I", 50) + b"short"
    with pytest.raises(ValueError):
        nh.read_message(io.BytesIO(framed))
