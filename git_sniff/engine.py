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
    GitSniffError,
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
    except GitSniffError:
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
