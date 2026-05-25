import os
import asyncio
import logging
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Query, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
import httpx

from git_sniff.schemas import RepoScorecard, PillarScores
from git_sniff.client import GitHubClient
from git_sniff.metrics import (
    calculate_maintenance_score,
    calculate_cicd_score,
    calculate_dependency_score,
    calculate_bus_factor_score,
    calculate_overall_score
)

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("git_sniff.server")

# Global holder for the lifespan-managed HTTP AsyncClient
class ServerState:
    http_client: Optional[httpx.AsyncClient] = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize shared AsyncClient
    ServerState.http_client = httpx.AsyncClient()
    logger.info("Shared HTTPX AsyncClient initialized for FastAPI lifespan.")
    yield
    # Clean up AsyncClient
    await ServerState.http_client.aclose()
    logger.info("Shared HTTPX AsyncClient closed.")

app = FastAPI(
    title="git-sniff Microservice API",
    description="Background microservice serving GitHub repository evaluation scorecards",
    version="0.1.0",
    lifespan=lifespan
)

# Configure CORSMiddleware
# Allows queries from extension background scripts (wildcards/localhost) and content scripts (github.com)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://github.com", "http://localhost", "http://127.0.0.1", "*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_gh_client() -> GitHubClient:
    """Dependency injection helper supplying the shared HTTPX client and Token."""
    token = os.getenv("GITHUB_PERSONAL_ACCESS_TOKEN")
    return GitHubClient(token=token, http_client=ServerState.http_client)

@app.get("/sniff", response_model=RepoScorecard)
async def sniff_repository(
    repo: str = Query(..., description="The repository formatted as owner/repo"),
    client: GitHubClient = Depends(get_gh_client)
):
    if "/" not in repo or len(repo.split("/")) != 2:
        raise HTTPException(
            status_code=400,
            detail="Invalid repository format. Please supply as owner/repo (e.g. langchain-ai/deepagents)"
        )
    owner, repo_name = repo.split("/")

    try:
        # Ingestion Flow
        # 1. Fetch Repository Metadata
        meta = await client.fetch_repo_metadata(owner, repo_name)
        default_branch = meta.get("default_branch", "main")
        stars = meta.get("stargazers_count", 0)
        open_issues = meta.get("open_issues_count", 0)

        # Fetch ingestion tasks concurrently using asyncio.gather
        issues_task = client.fetch_issues(owner, repo_name)
        tree_task = client.fetch_file_tree(owner, repo_name, default_branch)
        status_task = client.fetch_commit_status(owner, repo_name, default_branch)
        commits_task = client.fetch_commits(owner, repo_name, per_page=50)
        contribs_task = client.fetch_contributors(owner, repo_name)

        issues, file_paths, status, commits, contributors = await asyncio.gather(
            issues_task, tree_task, status_task, commits_task, contribs_task
        )

        # Fallback for contributor stats if compilation timed out/failed
        if not contributors:
            logger.info("Contributors stats compiling timed out or failed. Running commit authorship fallback...")
            fallback_commits = await client.fetch_commits(owner, repo_name, per_page=100)
            
            # Compile mock contributors list from commit profile logins/names
            author_commits = {}
            for c in fallback_commits:
                login = (c.get("author") or {}).get("login")
                name_info = (c.get("commit") or {}).get("author") or {}
                identifier = login or name_info.get("name") or "unknown"
                author_commits[identifier] = author_commits.get(identifier, 0) + 1
            
            contributors = [{"login": k, "contributions": v} for k, v in author_commits.items()]

        # Check manifest dependencies in recursive tree paths
        deps_count, pyproject_linting = await client.calculate_dependencies_count(
            owner, repo_name, default_branch, file_paths
        )

        # Calculate Pillars
        m_score, m_desc = calculate_maintenance_score(issues, stars, open_issues)
        c_score, c_desc = calculate_cicd_score(file_paths, status, pyproject_linting)
        d_score, d_desc = calculate_dependency_score(commits, deps_count, file_paths)
        b_score, b_desc = calculate_bus_factor_score(contributors)

        # Calculate Overall Scorecard
        overall, scorecard_status, recommendation = calculate_overall_score(
            maintenance=m_score,
            cicd=c_score,
            dependencies=d_score,
            bus_factor=b_score
        )

        # Surfacing warnings
        limit_warning = client.get_rate_limit_warning()

        return RepoScorecard(
            repository=f"{owner}/{repo_name}",
            overall_score=overall,
            status=scorecard_status,
            breakdown=PillarScores(
                maintenance=m_score,
                cicd=c_score,
                dependencies=d_score,
                bus_factor=b_score
            ),
            recommendation=recommendation,
            calculated_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            rate_limit_warning=limit_warning
        )

    except httpx.HTTPStatusError as e:
        if e.response.status_code == 403:
            raise HTTPException(
                status_code=403,
                detail="GitHub API rate limit exceeded. Please set the GITHUB_PERSONAL_ACCESS_TOKEN environment variable to bypass this."
            )
        raise HTTPException(
            status_code=e.response.status_code,
            detail=f"GitHub API Error: {str(e)}"
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception("An unexpected error occurred during API evaluation")
        raise HTTPException(
            status_code=500,
            detail=f"Internal evaluation engine error: {str(e)}"
        )
