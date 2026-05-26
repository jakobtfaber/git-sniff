import pytest
import httpx
import git_sniff.engine as engine_mod
from git_sniff.engine import evaluate, evaluate_detailed, parse_repo, Evaluation
from git_sniff.schemas import (
    GitSniffError, BadRepoError, RepoNotFoundError, RateLimitedError, EngineError,
    RepoScorecard,
)


def test_error_hierarchy():
    for cls in (BadRepoError, RepoNotFoundError, RateLimitedError, EngineError):
        assert issubclass(cls, GitSniffError)
    assert issubclass(GitSniffError, Exception)


class FakeClient:
    """Stand-in for GitHubClient with canned, healthy responses."""

    def __init__(self, *args, **kwargs):
        self.metadata_error = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def fetch_repo_metadata(self, owner, repo):
        if self.metadata_error:
            raise self.metadata_error
        return {"default_branch": "main", "stargazers_count": 100, "open_issues_count": 0}

    async def fetch_issues(self, owner, repo):
        return []

    async def fetch_file_tree(self, owner, repo, branch):
        return ["pyproject.toml", "poetry.lock", ".github/workflows/ci.yml"]

    async def fetch_commit_status(self, owner, repo, branch):
        return "success"

    async def fetch_commits(self, owner, repo, per_page=50):
        return []

    async def fetch_contributors(self, owner, repo):
        return [
            {"login": "a", "contributions": 40},
            {"login": "b", "contributions": 35},
            {"login": "c", "contributions": 25},
        ]

    async def calculate_dependencies_count(self, owner, repo, branch, file_paths):
        return (8, True)

    def get_rate_limit_warning(self):
        return None


def _patch_client(monkeypatch, factory):
    monkeypatch.setattr(engine_mod, "GitHubClient", factory)


def test_parse_repo_valid():
    assert parse_repo("langchain-ai/deepagents") == ("langchain-ai", "deepagents")


def test_parse_repo_rejects_malformed():
    with pytest.raises(BadRepoError):
        parse_repo("noslash")
    with pytest.raises(BadRepoError):
        parse_repo("a/b/c")


@pytest.mark.asyncio
async def test_evaluate_returns_scorecard(monkeypatch):
    _patch_client(monkeypatch, FakeClient)
    card = await evaluate("acme", "widget")
    assert isinstance(card, RepoScorecard)
    assert card.repository == "acme/widget"
    assert 0 <= card.overall_score <= 100
    assert card.status in ("HEALTHY", "WARNING", "CRITICAL")


@pytest.mark.asyncio
async def test_evaluate_detailed_has_descriptions(monkeypatch):
    _patch_client(monkeypatch, FakeClient)
    result = await evaluate_detailed("acme", "widget")
    assert isinstance(result, Evaluation)
    assert set(result.descriptions) == {"maintenance", "cicd", "dependencies", "bus_factor"}
    assert all(isinstance(v, str) and v for v in result.descriptions.values())


@pytest.mark.asyncio
async def test_evaluate_maps_not_found(monkeypatch):
    def factory(*a, **k):
        c = FakeClient()
        c.metadata_error = ValueError("Repository acme/missing not found or is private.")
        return c
    _patch_client(monkeypatch, factory)
    with pytest.raises(RepoNotFoundError):
        await evaluate("acme", "missing")


@pytest.mark.asyncio
async def test_evaluate_maps_rate_limit(monkeypatch):
    def factory(*a, **k):
        c = FakeClient()
        request = httpx.Request("GET", "https://api.github.com/repos/acme/widget")
        response = httpx.Response(403, request=request)
        c.metadata_error = httpx.HTTPStatusError("forbidden", request=request, response=response)
        return c
    _patch_client(monkeypatch, factory)
    with pytest.raises(RateLimitedError):
        await evaluate("acme", "widget")
