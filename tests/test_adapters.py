import json
import asyncio

import git_sniff.main as main_mod
from git_sniff.engine import Evaluation
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


def test_cli_uses_engine(monkeypatch, capsys):
    called = {}

    async def fake_detailed(owner, repo, *, token=None, http_client=None):
        called["args"] = (owner, repo)
        return Evaluation(
            scorecard=_scorecard(),
            descriptions={
                "maintenance": "median 4 days",
                "cicd": "workflows found",
                "dependencies": "lean",
                "bus_factor": "distributed",
            },
        )

    monkeypatch.setattr(main_mod, "evaluate_detailed", fake_detailed)
    monkeypatch.setattr(main_mod, "resolve_token", lambda: "ghp_x")
    asyncio.run(main_mod.sniff_cli("acme/widget"))
    out = capsys.readouterr().out
    assert called["args"] == ("acme", "widget")
    assert "GIT-SNIFF SCORECARD: acme/widget" in out
    assert "median 4 days" in out


def test_cli_json_success(monkeypatch, capsys):
    async def fake_evaluate(owner, repo, *, token=None, http_client=None):
        assert (owner, repo) == ("acme", "widget")
        return _scorecard()

    monkeypatch.setattr(main_mod, "evaluate", fake_evaluate)
    monkeypatch.setattr(main_mod, "resolve_token", lambda: None)
    rc = asyncio.run(main_mod.sniff_json("acme/widget"))
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["repository"] == "acme/widget"
    assert payload["overall_score"] == 88


def test_cli_json_engine_error(monkeypatch, capsys):
    async def fake_evaluate(owner, repo, *, token=None, http_client=None):
        raise RepoNotFoundError("missing")

    monkeypatch.setattr(main_mod, "evaluate", fake_evaluate)
    monkeypatch.setattr(main_mod, "resolve_token", lambda: None)
    rc = asyncio.run(main_mod.sniff_json("acme/missing"))
    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"] == "missing"


def test_cli_json_bad_format(monkeypatch, capsys):
    monkeypatch.setattr(main_mod, "resolve_token", lambda: None)
    rc = asyncio.run(main_mod.sniff_json("noslash"))
    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert "error" in payload
