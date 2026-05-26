import pytest
from fastapi.testclient import TestClient

import git_sniff.server as server_mod
from git_sniff.schemas import (
    RepoScorecard, PillarScores,
    BadRepoError, RepoNotFoundError, RateLimitedError, EngineError,
)


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


def test_server_success(monkeypatch):
    async def fake_evaluate(owner, repo, *, token=None, http_client=None):
        assert (owner, repo) == ("acme", "widget")
        return _scorecard()
    monkeypatch.setattr(server_mod, "evaluate", fake_evaluate)
    with TestClient(server_mod.app) as tc:
        r = tc.get("/sniff", params={"repo": "acme/widget"})
    assert r.status_code == 200
    assert r.json()["overall_score"] == 88


@pytest.mark.parametrize("exc,code", [
    (BadRepoError("bad"), 400),
    (RepoNotFoundError("missing"), 404),
    (RateLimitedError("limited"), 403),
    (EngineError("boom"), 500),
])
def test_server_error_mapping(monkeypatch, exc, code):
    async def fake_evaluate(owner, repo, *, token=None, http_client=None):
        raise exc
    monkeypatch.setattr(server_mod, "evaluate", fake_evaluate)
    with TestClient(server_mod.app) as tc:
        r = tc.get("/sniff", params={"repo": "acme/widget"})
    assert r.status_code == code
    assert "detail" in r.json()


def test_server_bad_format_short_circuits(monkeypatch):
    with TestClient(server_mod.app) as tc:
        r = tc.get("/sniff", params={"repo": "noslash"})
    assert r.status_code == 400
