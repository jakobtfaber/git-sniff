# Native Messaging Host Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor the git-sniff Chrome extension off localhost HTTP onto Chrome Native Messaging — a shared `engine.evaluate()` core with three thin adapters (FastAPI legacy, CLI, new stdio native host), Keychain-first auth, and an idempotent host-manifest installer.

**Architecture:** Extract the orchestration currently duplicated in `server.py` and `main.py` into `git_sniff/engine.py`. Add `git_sniff/auth.py` (token resolution) and `git_sniff/native_host.py` (stdio framing + Chrome manifest install). The extension's `background.js` swaps `fetch` for `chrome.runtime.sendNativeMessage`; `content.js` is untouched. FastAPI `--server` is retained but deprecated.

**Tech Stack:** Python 3.10+ (asyncio, httpx, pydantic, FastAPI/uvicorn, Rich), pytest, Chrome MV3 (service worker, native messaging). macOS + Google Chrome target.

**Spec:** `docs/superpowers/specs/2026-05-26-native-messaging-host-design.md`
**ADR:** `docs/decisions/0001-native-messaging-transport.md`

**Conventions for this repo:** conda env `py312`, package installed editable (`pip install -e ".[dev]"`). Run tests with `python -m pytest`. No code comments unless a step's code shows them. The existing 15 tests in `tests/test_metrics.py` must keep passing.

---

## File Structure

- Create `git_sniff/engine.py` — transport-agnostic orchestration: `evaluate()`, `evaluate_detailed()`, `parse_repo()`, `Evaluation` dataclass.
- Create `git_sniff/auth.py` — `resolve_token()` (Keychain → env → None).
- Create `git_sniff/native_host.py` — framing (`encode_message`/`read_message`/`write_message`), host loop (`run_host`/`_handle`), manifest install/uninstall/status, `main()`.
- Modify `git_sniff/schemas.py` — add `GitSniffError` hierarchy.
- Modify `git_sniff/server.py` — route becomes a thin `evaluate()` shim with error→HTTP mapping.
- Modify `git_sniff/main.py` — `sniff_cli` calls `evaluate_detailed()` + `resolve_token()`.
- Modify `pyproject.toml` — add `git-sniff-host` console entry.
- Modify `extension/manifest.json` — drop localhost `host_permissions`; add `nativeMessaging` + pinned `key`.
- Modify `extension/background.js` — `sendNativeMessage` instead of `fetch`.
- Modify `.gitignore` — exclude the generated private key.
- Create `tests/test_engine.py`, `tests/test_auth.py`, `tests/test_native_host.py`, `tests/test_adapters.py`.
- Modify `README.md` — install/registration/deprecation docs.

---

## Task 1: Typed error hierarchy in schemas

**Files:**
- Modify: `git_sniff/schemas.py`
- Test: `tests/test_engine.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_engine.py`:

```python
from git_sniff.schemas import (
    GitSniffError, BadRepoError, RepoNotFoundError, RateLimitedError, EngineError
)


def test_error_hierarchy():
    for cls in (BadRepoError, RepoNotFoundError, RateLimitedError, EngineError):
        assert issubclass(cls, GitSniffError)
    assert issubclass(GitSniffError, Exception)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_engine.py::test_error_hierarchy -v`
Expected: FAIL with `ImportError: cannot import name 'GitSniffError'`.

- [ ] **Step 3: Add the error classes**

Append to `git_sniff/schemas.py`:

```python
class GitSniffError(Exception):
    """Base class for all git-sniff engine errors."""


class BadRepoError(GitSniffError):
    """Malformed owner/repo input."""


class RepoNotFoundError(GitSniffError):
    """Repository does not exist or is private."""


class RateLimitedError(GitSniffError):
    """GitHub API rate limit exceeded."""


class EngineError(GitSniffError):
    """Unexpected internal evaluation failure."""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_engine.py::test_error_hierarchy -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add git_sniff/schemas.py tests/test_engine.py
git commit -m "Add GitSniffError hierarchy to schemas"
```

---

## Task 2: Shared orchestration engine

**Files:**
- Create: `git_sniff/engine.py`
- Test: `tests/test_engine.py`

The engine holds the orchestration currently in `server.py:74-143` and `main.py:36-86`. `evaluate()` returns a `RepoScorecard` (the contract for server + native host). `evaluate_detailed()` additionally returns the four pillar description strings the CLI needs. Both go through one orchestration path, so adapters stay in parity. The engine is **auth-agnostic** (caller passes `token`) and owns the client lifecycle only when no `http_client` is injected.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_engine.py`:

```python
import pytest
import httpx
import git_sniff.engine as engine_mod
from git_sniff.engine import evaluate, evaluate_detailed, parse_repo, Evaluation
from git_sniff.schemas import RepoScorecard, BadRepoError, RepoNotFoundError, RateLimitedError


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
```

This needs `pytest-asyncio`. Add it in Step 2.

- [ ] **Step 2: Add async test dependency and config**

Modify `pyproject.toml` — add to `[project.optional-dependencies].dev`:

```toml
dev = [
    "pytest>=7.0.0",
    "pytest-asyncio>=0.23.0",
]
```

Append to `pyproject.toml`:

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
```

Then: `pip install -e ".[dev]"`

- [ ] **Step 3: Run tests to verify they fail**

Run: `python -m pytest tests/test_engine.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'git_sniff.engine'`.

- [ ] **Step 4: Write the engine**

Create `git_sniff/engine.py`:

```python
import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple

import httpx

from git_sniff.client import GitHubClient
from git_sniff.metrics import (
    calculate_maintenance_score,
    calculate_cicd_score,
    calculate_dependency_score,
    calculate_bus_factor_score,
    calculate_overall_score,
)
from git_sniff.schemas import (
    RepoScorecard,
    PillarScores,
    BadRepoError,
    RepoNotFoundError,
    RateLimitedError,
    EngineError,
)

logger = logging.getLogger("git_sniff.engine")


@dataclass
class Evaluation:
    scorecard: RepoScorecard
    descriptions: Dict[str, str]


def parse_repo(repo: str) -> Tuple[str, str]:
    parts = repo.split("/")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise BadRepoError(
            "Invalid repository format. Please supply as owner/repo "
            "(e.g. langchain-ai/deepagents)"
        )
    return parts[0], parts[1]


async def _orchestrate(client: GitHubClient, owner: str, repo: str) -> Evaluation:
    meta = await client.fetch_repo_metadata(owner, repo)
    default_branch = meta.get("default_branch", "main")
    stars = meta.get("stargazers_count", 0)
    open_issues = meta.get("open_issues_count", 0)

    issues, file_paths, status, commits, contributors = await asyncio.gather(
        client.fetch_issues(owner, repo),
        client.fetch_file_tree(owner, repo, default_branch),
        client.fetch_commit_status(owner, repo, default_branch),
        client.fetch_commits(owner, repo, per_page=50),
        client.fetch_contributors(owner, repo),
    )

    if not contributors:
        logger.info("Contributor stats unavailable; running commit-authorship fallback.")
        fallback_commits = await client.fetch_commits(owner, repo, per_page=100)
        author_commits: Dict[str, int] = {}
        for c in fallback_commits:
            login = (c.get("author") or {}).get("login")
            name_info = (c.get("commit") or {}).get("author") or {}
            identifier = login or name_info.get("name") or "unknown"
            author_commits[identifier] = author_commits.get(identifier, 0) + 1
        contributors = [{"login": k, "contributions": v} for k, v in author_commits.items()]

    deps_count, pyproject_linting = await client.calculate_dependencies_count(
        owner, repo, default_branch, file_paths
    )

    m_score, m_desc = calculate_maintenance_score(issues, stars, open_issues)
    c_score, c_desc = calculate_cicd_score(file_paths, status, pyproject_linting)
    d_score, d_desc = calculate_dependency_score(commits, deps_count, file_paths)
    b_score, b_desc = calculate_bus_factor_score(contributors)

    overall, scorecard_status, recommendation = calculate_overall_score(
        maintenance=m_score, cicd=c_score, dependencies=d_score, bus_factor=b_score
    )

    scorecard = RepoScorecard(
        repository=f"{owner}/{repo}",
        overall_score=overall,
        status=scorecard_status,
        breakdown=PillarScores(
            maintenance=m_score, cicd=c_score, dependencies=d_score, bus_factor=b_score
        ),
        recommendation=recommendation,
        calculated_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        rate_limit_warning=client.get_rate_limit_warning(),
    )
    return Evaluation(
        scorecard=scorecard,
        descriptions={
            "maintenance": m_desc,
            "cicd": c_desc,
            "dependencies": d_desc,
            "bus_factor": b_desc,
        },
    )


async def evaluate_detailed(
    owner: str,
    repo: str,
    *,
    token: Optional[str] = None,
    http_client: Optional[httpx.AsyncClient] = None,
) -> Evaluation:
    try:
        if http_client is not None:
            client = GitHubClient(token=token, http_client=http_client)
            return await _orchestrate(client, owner, repo)
        async with GitHubClient(token=token) as client:
            return await _orchestrate(client, owner, repo)
    except RepoNotFoundError:
        raise
    except ValueError as e:
        raise RepoNotFoundError(str(e)) from e
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 403:
            raise RateLimitedError(
                "GitHub API rate limit exceeded. Set GITHUB_PERSONAL_ACCESS_TOKEN "
                "or store a token in the Keychain (service 'Agents', account 'github-pat')."
            ) from e
        raise EngineError(f"GitHub API error: {e}") from e
    except Exception as e:
        raise EngineError(f"Internal evaluation engine error: {e}") from e


async def evaluate(
    owner: str,
    repo: str,
    *,
    token: Optional[str] = None,
    http_client: Optional[httpx.AsyncClient] = None,
) -> RepoScorecard:
    result = await evaluate_detailed(owner, repo, token=token, http_client=http_client)
    return result.scorecard
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_engine.py -v`
Expected: PASS (6 tests).

- [ ] **Step 6: Run the full suite (regression check)**

Run: `python -m pytest -v`
Expected: PASS — 15 existing metrics tests + 6 engine tests.

- [ ] **Step 7: Commit**

```bash
git add git_sniff/engine.py tests/test_engine.py pyproject.toml
git commit -m "Add shared evaluate() orchestration engine"
```

---

## Task 3: Token resolver (Keychain → env → None)

**Files:**
- Create: `git_sniff/auth.py`
- Test: `tests/test_auth.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_auth.py`:

```python
import subprocess
import git_sniff.auth as auth_mod
from git_sniff.auth import resolve_token


class _CompletedProcess:
    def __init__(self, returncode, stdout=""):
        self.returncode = returncode
        self.stdout = stdout


def test_keychain_hit(monkeypatch):
    monkeypatch.delenv("GITHUB_PERSONAL_ACCESS_TOKEN", raising=False)
    monkeypatch.setattr(
        auth_mod.subprocess, "run",
        lambda *a, **k: _CompletedProcess(0, "ghp_keychaintoken\n"),
    )
    assert resolve_token() == "ghp_keychaintoken"


def test_env_fallback_when_keychain_misses(monkeypatch):
    monkeypatch.setattr(
        auth_mod.subprocess, "run",
        lambda *a, **k: _CompletedProcess(44, ""),
    )
    monkeypatch.setenv("GITHUB_PERSONAL_ACCESS_TOKEN", "ghp_envtoken")
    assert resolve_token() == "ghp_envtoken"


def test_none_when_neither(monkeypatch):
    monkeypatch.setattr(
        auth_mod.subprocess, "run",
        lambda *a, **k: _CompletedProcess(44, ""),
    )
    monkeypatch.delenv("GITHUB_PERSONAL_ACCESS_TOKEN", raising=False)
    assert resolve_token() is None


def test_keychain_exception_falls_through(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError("security not found")
    monkeypatch.setattr(auth_mod.subprocess, "run", boom)
    monkeypatch.setenv("GITHUB_PERSONAL_ACCESS_TOKEN", "ghp_envtoken")
    assert resolve_token() == "ghp_envtoken"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_auth.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'git_sniff.auth'`.

- [ ] **Step 3: Write the resolver**

Create `git_sniff/auth.py`:

```python
import os
import subprocess
from typing import Optional

KEYCHAIN_SERVICE = "Agents"
KEYCHAIN_ACCOUNT = "github-pat"
ENV_VAR = "GITHUB_PERSONAL_ACCESS_TOKEN"


def _keychain_token() -> Optional[str]:
    try:
        result = subprocess.run(
            [
                "/usr/bin/security", "find-generic-password",
                "-s", KEYCHAIN_SERVICE,
                "-a", KEYCHAIN_ACCOUNT,
                "-w",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return None
    if result.returncode == 0:
        token = result.stdout.strip()
        return token or None
    return None


def resolve_token() -> Optional[str]:
    """Resolve a GitHub token: macOS Keychain first, then env var, else None."""
    token = _keychain_token()
    if token:
        return token
    return os.getenv(ENV_VAR) or None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_auth.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add git_sniff/auth.py tests/test_auth.py
git commit -m "Add Keychain-first token resolver"
```

---

## Task 4: FastAPI adapter calls the engine (legacy/deprecated)

**Files:**
- Modify: `git_sniff/server.py`
- Test: `tests/test_adapters.py`

The route becomes a thin shim: `parse_repo` → `resolve_token` → `evaluate(http_client=shared)` → map `GitSniffError` to the existing HTTP codes. The shared lifespan `httpx.AsyncClient` is preserved.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_adapters.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_adapters.py -v`
Expected: FAIL — the route still does its own orchestration; `server_mod.evaluate` does not exist, so `monkeypatch.setattr` raises `AttributeError`.

- [ ] **Step 3: Rewrite the server route**

Replace the entire contents of `git_sniff/server.py` with:

```python
import logging
from typing import Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import httpx

from git_sniff.schemas import (
    RepoScorecard,
    BadRepoError,
    RepoNotFoundError,
    RateLimitedError,
    EngineError,
)
from git_sniff.engine import evaluate, parse_repo
from git_sniff.auth import resolve_token

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("git_sniff.server")


class ServerState:
    http_client: Optional[httpx.AsyncClient] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    ServerState.http_client = httpx.AsyncClient()
    logger.info("Shared HTTPX AsyncClient initialized.")
    yield
    await ServerState.http_client.aclose()
    logger.info("Shared HTTPX AsyncClient closed.")


app = FastAPI(
    title="git-sniff Microservice API (DEPRECATED)",
    description=(
        "Legacy/manual HTTP adapter for the git-sniff engine. The Chrome extension "
        "now uses Native Messaging; this server is retained for CLI scripting and "
        "curl. Scheduled for removal once sniff.sh migrates off HTTP."
    ),
    version="0.2.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://github.com", "http://localhost", "http://127.0.0.1", "*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/sniff", response_model=RepoScorecard)
async def sniff_repository(repo: str = Query(..., description="owner/repo")):
    try:
        owner, repo_name = parse_repo(repo)
        return await evaluate(
            owner, repo_name,
            token=resolve_token(),
            http_client=ServerState.http_client,
        )
    except BadRepoError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RepoNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RateLimitedError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except EngineError as e:
        raise HTTPException(status_code=500, detail=str(e))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_adapters.py -v`
Expected: PASS (6 parametrized + cases).

- [ ] **Step 5: Run the full suite**

Run: `python -m pytest -v`
Expected: PASS (all prior + new).

- [ ] **Step 6: Commit**

```bash
git add git_sniff/server.py tests/test_adapters.py
git commit -m "Refactor FastAPI adapter onto shared engine; mark deprecated"
```

---

## Task 5: CLI adapter calls the engine

**Files:**
- Modify: `git_sniff/main.py`
- Test: `tests/test_adapters.py`

`sniff_cli` keeps its Rich scorecard layout but sources data from `evaluate_detailed()` (for the pillar description strings) and `resolve_token()`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_adapters.py`:

```python
import asyncio
import git_sniff.main as main_mod
from git_sniff.engine import Evaluation


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_adapters.py::test_cli_uses_engine -v`
Expected: FAIL — `main_mod.evaluate_detailed` / `resolve_token` not importable there.

- [ ] **Step 3: Rewrite `sniff_cli` and imports in `main.py`**

Replace `git_sniff/main.py` lines 1-93 (imports through the end of the `sniff_cli` ingestion block) with:

```python
import sys
import argparse
import asyncio
import logging

import uvicorn
from rich.console import Console

from git_sniff.engine import evaluate_detailed, parse_repo
from git_sniff.auth import resolve_token
from git_sniff.schemas import BadRepoError, RepoNotFoundError, RateLimitedError, EngineError

console = Console()
logging.basicConfig(level=logging.WARNING)


async def sniff_cli(repo: str):
    try:
        owner, repo_name = parse_repo(repo)
    except BadRepoError as e:
        console.print(f"[bold red]Error: {e}[/bold red]")
        sys.exit(2)

    with console.status(f"[bold blue]Sniffing repository {owner}/{repo_name}...[/bold blue]"):
        try:
            result = await evaluate_detailed(owner, repo_name, token=resolve_token())
        except RateLimitedError as e:
            console.print(f"[bold yellow]{e}[/bold yellow]")
            sys.exit(1)
        except (RepoNotFoundError, EngineError) as e:
            console.print(f"[bold red]Error: {e}[/bold red]")
            sys.exit(1)

    card = result.scorecard
    desc = result.descriptions
    overall = card.overall_score
    scorecard_status = card.status
    recommendation = card.recommendation
    limit_warning = card.rate_limit_warning
    m_score = card.breakdown.maintenance
    c_score = card.breakdown.cicd
    d_score = card.breakdown.dependencies
    b_score = card.breakdown.bus_factor
    m_desc = desc["maintenance"]
    c_desc = desc["cicd"]
    d_desc = desc["dependencies"]
    b_desc = desc["bus_factor"]
```

Leave the rest of `main.py` from the `# Helper formatters` comment (current line 95) through `main()` unchanged — the local `get_color_tag`/`get_status_tag` helpers and all `console.print(...)` lines already reference the variables rebound above.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_adapters.py::test_cli_uses_engine -v`
Expected: PASS.

- [ ] **Step 5: Smoke-test the real CLI**

Run: `git-sniff langchain-ai/deepagents`
Expected: a rendered scorecard (requires network + token; if unauthenticated, a rate-limit warning line is acceptable). No traceback.

- [ ] **Step 6: Run the full suite**

Run: `python -m pytest -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add git_sniff/main.py tests/test_adapters.py
git commit -m "Refactor CLI adapter onto shared engine"
```

---

## Task 6: Register the `git-sniff-host` console entry point

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add the console script**

In `pyproject.toml`, change `[project.scripts]` to:

```toml
[project.scripts]
git-sniff = "git_sniff.main:main"
git-sniff-host = "git_sniff.native_host:main"
```

- [ ] **Step 2: Create a placeholder so the entry resolves**

Create `git_sniff/native_host.py`:

```python
def main():
    raise SystemExit("git-sniff-host not yet implemented")
```

- [ ] **Step 3: Reinstall and verify the binary exists**

Run: `pip install -e ".[dev]" && which git-sniff-host`
Expected: an absolute path inside the active env's `bin/` (e.g. `.../envs/py312/bin/git-sniff-host`).

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml git_sniff/native_host.py
git commit -m "Register git-sniff-host console entry point"
```

---

## Task 7: Native messaging framing + host loop

**Files:**
- Modify: `git_sniff/native_host.py`
- Test: `tests/test_native_host.py`

Implements length-prefixed framing (`@I` = native byte order), the one-shot host handler with a 30s `asyncio.wait_for` deadline, error-as-`{"error": …}` replies, and strict binary stdout discipline.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_native_host.py`:

```python
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
    # stdout discipline: exactly one framed message, nothing trailing
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_native_host.py -v`
Expected: FAIL — `encode_message`/`read_message`/`_handle` not defined (placeholder module).

- [ ] **Step 3: Implement framing + handler**

Replace `git_sniff/native_host.py` with:

```python
import sys
import json
import struct
import asyncio
import logging
from typing import Optional

from git_sniff.engine import evaluate
from git_sniff.auth import resolve_token
from git_sniff.schemas import BadRepoError, GitSniffError

logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
logger = logging.getLogger("git_sniff.native_host")

HOST_TIMEOUT = 30


def encode_message(obj) -> bytes:
    data = json.dumps(obj).encode("utf-8")
    return struct.pack("@I", len(data)) + data


def read_message(stream) -> Optional[dict]:
    raw_len = stream.read(4)
    if len(raw_len) < 4:
        return None
    (length,) = struct.unpack("@I", raw_len)
    data = stream.read(length)
    return json.loads(data.decode("utf-8"))


def write_message(stream, obj) -> None:
    stream.write(encode_message(obj))
    stream.flush()


async def _handle(stdin_buf, stdout_buf) -> None:
    message = read_message(stdin_buf)
    if message is None:
        return
    try:
        owner = message.get("owner")
        repo = message.get("repo")
        if not owner or not repo:
            raise BadRepoError("Request must include non-empty 'owner' and 'repo'.")
        scorecard = await asyncio.wait_for(
            evaluate(owner, repo, token=resolve_token()),
            timeout=HOST_TIMEOUT,
        )
        write_message(stdout_buf, scorecard.model_dump())
    except asyncio.TimeoutError:
        write_message(stdout_buf, {
            "error": "Connection timed out. GitHub statistics took too long to compile."
        })
    except GitSniffError as e:
        write_message(stdout_buf, {"error": str(e)})
    except Exception as e:
        logger.exception("Unexpected native host failure")
        write_message(stdout_buf, {"error": f"git-sniff host error: {e}"})


def run_host() -> None:
    asyncio.run(_handle(sys.stdin.buffer, sys.stdout.buffer))


def main():
    run_host()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_native_host.py -v`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add git_sniff/native_host.py tests/test_native_host.py
git commit -m "Implement native messaging framing and one-shot host loop"
```

---

## Task 8: Host manifest install / uninstall / status

**Files:**
- Modify: `git_sniff/native_host.py`
- Test: `tests/test_native_host.py`

`EXTENSION_ID` is a placeholder constant here; Task 9 replaces it with the real derived ID. The installer writes atomically (`os.replace`) and idempotently.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_native_host.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_native_host.py -k "manifest or install or uninstall or status" -v`
Expected: FAIL — `build_manifest`/`install`/`CHROME_NM_DIR` not defined.

- [ ] **Step 3: Implement installer + argparse main**

In `git_sniff/native_host.py`, add these imports at the top (alongside existing):

```python
import os
import shutil
import argparse
from pathlib import Path
```

Add after the `HOST_TIMEOUT = 30` line:

```python
HOST_NAME = "com.jakobtfaber.git_sniff"
EXTENSION_ID = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"  # placeholder; set in Task 9
CHROME_NM_DIR = (
    Path.home()
    / "Library" / "Application Support" / "Google" / "Chrome" / "NativeMessagingHosts"
)


def manifest_path() -> Path:
    return CHROME_NM_DIR / f"{HOST_NAME}.json"


def host_binary_path() -> Optional[str]:
    found = shutil.which("git-sniff-host")
    return os.path.realpath(found) if found else None


def build_manifest(path: str) -> dict:
    return {
        "name": HOST_NAME,
        "description": "git-sniff native messaging host",
        "path": path,
        "type": "stdio",
        "allowed_origins": [f"chrome-extension://{EXTENSION_ID}/"],
    }


def install() -> None:
    path = host_binary_path()
    if not path:
        raise SystemExit(
            "git-sniff-host not found on PATH. Run: pip install -e . in the git-sniff repo."
        )
    CHROME_NM_DIR.mkdir(parents=True, exist_ok=True)
    target = manifest_path()
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(json.dumps(build_manifest(path), indent=2))
    os.replace(tmp, target)
    print(f"Installed native host manifest: {target}")
    print(f"  path:           {path}")
    print(f"  allowed origin: chrome-extension://{EXTENSION_ID}/")


def uninstall() -> None:
    target = manifest_path()
    try:
        target.unlink()
        print(f"Removed {target}")
    except FileNotFoundError:
        print(f"Nothing to remove at {target}")


def status() -> None:
    target = manifest_path()
    print(f"Host name:        {HOST_NAME}")
    print(f"Expected origin:  chrome-extension://{EXTENSION_ID}/")
    print(f"Manifest path:    {target}")
    binary = host_binary_path()
    print(f"Resolved binary:  {binary or '(git-sniff-host not on PATH)'}")
    if binary:
        print(f"  exists/executable: {os.path.isfile(binary) and os.access(binary, os.X_OK)}")
    if not target.exists():
        print("Manifest: NOT INSTALLED (run: git-sniff-host --install)")
        return
    data = json.loads(target.read_text())
    origins = data.get("allowed_origins", [])
    expected = f"chrome-extension://{EXTENSION_ID}/"
    if expected in origins:
        print("Origin: OK (matches expected extension ID)")
    else:
        print(f"Origin: DRIFT/MISMATCH — manifest has {origins}, expected {expected}")
    print(f"Registered path: {data.get('path')}")
```

Replace the existing `def main():` body with an argparse dispatcher:

```python
def main():
    parser = argparse.ArgumentParser(
        prog="git-sniff-host",
        description="git-sniff Chrome Native Messaging host.",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--install", action="store_true", help="Write/update the Chrome native-host manifest.")
    group.add_argument("--uninstall", action="store_true", help="Remove the manifest.")
    group.add_argument("--status", action="store_true", help="Print manifest/host/origin status.")
    args, _ = parser.parse_known_args()

    if args.install:
        install()
    elif args.uninstall:
        uninstall()
    elif args.status:
        status()
    else:
        run_host()
```

Note: `parse_known_args` ignores Chrome's appended origin argv so the no-flag spawn lands in `run_host()`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_native_host.py -v`
Expected: PASS (all native-host tests).

- [ ] **Step 5: Verify status against the real env**

Run: `git-sniff-host --status`
Expected: prints host name, expected origin (placeholder ID for now), resolved binary path that exists/executable=True, manifest NOT INSTALLED.

- [ ] **Step 6: Commit**

```bash
git add git_sniff/native_host.py tests/test_native_host.py
git commit -m "Add native host manifest install/uninstall/status"
```

---

## Task 9: Generate the pinned extension key + ID

**Files:**
- Create (uncommitted): `extension/key.pem` (private key — gitignored)
- Modify: `git_sniff/native_host.py` (set real `EXTENSION_ID`)
- Modify: `extension/manifest.json` (add real `key`) — applied in Task 10
- Modify: `.gitignore`

This produces the deterministic key/ID pair. The private key is never committed; only the public `key` (in the extension manifest) and the derived ID (in source) are.

- [ ] **Step 1: Ignore the private key first**

Append to `.gitignore`:

```
extension/key.pem
```

- [ ] **Step 2: Generate the keypair**

Run from the repo root:

```bash
openssl genrsa 2048 | openssl pkcs8 -topk8 -nocrypt -out extension/key.pem
```

Expected: `extension/key.pem` created (PKCS#8 RSA private key).

- [ ] **Step 3: Compute the base64 public `key` for the manifest**

Run:

```bash
openssl rsa -in extension/key.pem -pubout -outform DER 2>/dev/null | openssl base64 -A; echo
```

Expected: one long base64 line. Record it as `MANIFEST_KEY` — used in Task 10's `manifest.json`.

- [ ] **Step 4: Derive the extension ID**

Run:

```bash
openssl rsa -in extension/key.pem -pubout -outform DER 2>/dev/null \
  | openssl dgst -sha256 -binary \
  | head -c 16 \
  | xxd -p \
  | tr '0-9a-f' 'a-p'
```

Expected: a 32-character string using only letters `a`–`p` (e.g. `mbnp...`). Record it as `EXT_ID`.

- [ ] **Step 5: Set the real `EXTENSION_ID`**

In `git_sniff/native_host.py`, replace:

```python
EXTENSION_ID = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"  # placeholder; set in Task 9
```

with (substituting the recorded `EXT_ID`):

```python
EXTENSION_ID = "EXT_ID"  # pinned via extension/manifest.json "key"
```

- [ ] **Step 6: Fix the drift test's stale ID**

The `test_status_reports_origin_drift` test uses a hardcoded wrong 32-char origin; confirm it differs from the real `EXT_ID` (it uses `stalewrongid...`, which will). Re-run:

Run: `python -m pytest tests/test_native_host.py -v`
Expected: PASS.

- [ ] **Step 7: Commit (source only — NOT the key)**

```bash
git add git_sniff/native_host.py .gitignore
git status   # confirm extension/key.pem is NOT staged
git commit -m "Pin extension ID in native host"
```

---

## Task 10: Switch the extension to native messaging

**Files:**
- Modify: `extension/manifest.json`
- Modify: `extension/background.js`

`content.js` is intentionally untouched.

- [ ] **Step 1: Rewrite `manifest.json`**

Replace `extension/manifest.json` with (substitute the recorded `MANIFEST_KEY` from Task 9 Step 3):

```json
{
  "manifest_version": 3,
  "name": "git-sniff Scorecard",
  "version": "2.0.0",
  "description": "Premium git-sniff scorecard on GitHub, served by an on-demand native messaging host.",
  "key": "MANIFEST_KEY",
  "permissions": [
    "storage",
    "nativeMessaging"
  ],
  "background": {
    "service_worker": "background.js"
  },
  "content_scripts": [
    {
      "matches": ["https://github.com/*"],
      "js": ["content.js"],
      "run_at": "document_idle"
    }
  ],
  "web_accessible_resources": [
    {
      "resources": ["content.css"],
      "matches": ["https://github.com/*"]
    }
  ],
  "action": {}
}
```

- [ ] **Step 2: Rewrite `background.js`**

Replace `extension/background.js` with:

```javascript
// background.js - Ephemeral Service Worker (Native Messaging transport)

const HOST_NAME = "com.jakobtfaber.git_sniff";

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.action === 'fetchScorecard') {
    console.log(`[git-sniff] Querying native host for ${message.owner}/${message.repo}`);

    chrome.runtime.sendNativeMessage(
      HOST_NAME,
      { owner: message.owner, repo: message.repo },
      (response) => {
        if (chrome.runtime.lastError) {
          console.error('[git-sniff] Native host error:', chrome.runtime.lastError.message);
          sendResponse({
            success: false,
            error: 'Native host not installed. Run: git-sniff-host --install'
          });
          return;
        }
        if (!response) {
          sendResponse({ success: false, error: 'No response from the git-sniff native host.' });
          return;
        }
        if (response.error) {
          sendResponse({ success: false, error: response.error });
          return;
        }
        sendResponse({ success: true, data: response });
      }
    );

    return true; // keep the message channel open for the async sendResponse
  }
});
```

- [ ] **Step 3: Install the host manifest for the real extension ID**

Run: `git-sniff-host --install && git-sniff-host --status`
Expected: manifest written; `--status` shows "Origin: OK".

- [ ] **Step 4: Manual end-to-end verification (no CI — `chrome://` is unreachable to automation)**

Document and perform manually:
1. Load the unpacked extension at `chrome://extensions` (Developer mode → Load unpacked → `extension/`). Confirm the extension ID matches the pinned `EXT_ID` from Task 9.
2. Accept the "Communicate with cooperating native applications" permission.
3. Open a GitHub repo page (e.g. `https://github.com/langchain-ai/deepagents`).
4. Confirm the scorecard pill renders with a real score (no "Native host not installed" / no timeout).
5. Navigate to another repo via in-page links; confirm the pill re-renders (SPA detection in `content.js` unchanged).

Expected: pill renders scores end-to-end through the native host; no localhost server running.

- [ ] **Step 5: Commit**

```bash
git add extension/manifest.json extension/background.js
git commit -m "Switch extension to native messaging transport"
```

---

## Task 11: Documentation — install, deprecation, removal criteria

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update the Server Mode + add a Native Messaging section**

In `README.md`, under "### 2. Server Mode" (around line 34), prepend a deprecation note:

```markdown
> **Deprecated.** The Chrome extension no longer uses this HTTP server — it uses Chrome
> Native Messaging (see below). `--server` is retained only for CLI scripting / curl and
> will be removed once `skills/repo-hygiene/scripts/sniff.sh` migrates off HTTP.
```

After the "### 2. Server Mode" block, add:

```markdown
### 3. Chrome extension (Native Messaging)

The extension talks to a local host that Chrome spawns on demand — no server, no open
port. Register the host once:

```bash
pip install -e .            # provides the git-sniff-host binary
git-sniff-host --install    # writes the Chrome native-host manifest
git-sniff-host --status     # verify path + allowed origin
```

Then load `extension/` unpacked at `chrome://extensions`. The host reads its GitHub token
from the macOS Keychain (service `Agents`, account `github-pat`), falling back to the
`GITHUB_PERSONAL_ACCESS_TOKEN` environment variable.

Re-run `git-sniff-host --install` after any Python/venv reinstall (the manifest records an
absolute path that a reinstall can invalidate). `git-sniff-host --uninstall` removes it.
```

- [ ] **Step 2: Run the full suite one last time**

Run: `python -m pytest -v`
Expected: PASS — all tests (metrics 15 + engine + auth + adapters + native host).

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "Document native-messaging install and HTTP-server deprecation"
```

---

## Done criteria

- `python -m pytest` green: existing 15 metrics tests + new engine/auth/adapter/native-host tests.
- `git-sniff owner/repo` renders a scorecard via the shared engine (CLI parity).
- `git-sniff --server` still serves `/sniff` (deprecated legacy adapter), unchanged HTTP status codes.
- `git-sniff-host --install` registers the manifest; `--status` reports OK; the extension renders scorecards end-to-end through the native host with **no** localhost server and **no** `host_permissions`.
- `content.js` unchanged; `background.js`↔`content.js` message contract preserved.
- Private key (`extension/key.pem`) is gitignored and never committed.

## Follow-ups (out of scope — surface, don't implement)

- Migrate `skills/repo-hygiene/scripts/sniff.sh` to a `git-sniff --json owner/repo` CLI mode, then execute ADR 0001's removal criteria (delete `--server`, `server.py`, fastapi/uvicorn deps).
- Chrome-for-Testing / Chromium / cross-browser host registration (installer is structured for it via `CHROME_NM_DIR`).
