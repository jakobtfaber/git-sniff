import base64
import json
import asyncio
import logging
from typing import Optional, Dict, Any, List, Tuple
import httpx

try:
    import tomllib
except ImportError:
    import tomli as tomllib

from git_sniff.metrics import (
    check_pyproject_linting,
    count_pyproject_deps,
    count_cargo_deps
)

logger = logging.getLogger("git_sniff.client")

class GitHubClient:
    """
    Async client for integrating with the GitHub REST API.
    Supports token auth, rate-limit sniffing, non-blocking 202 retries,
    recursive directory scans, and manifest parsing.
    """
    def __init__(self, token: Optional[str] = None, http_client: Optional[httpx.AsyncClient] = None):
        self.token = token
        self._owned_http_client = None
        if http_client:
            self.http_client = http_client
        else:
            self.http_client = None
        self.rate_limit_remaining: Optional[int] = None
        self.rate_limit_reset: Optional[int] = None

    async def __aenter__(self):
        if not self.http_client:
            self._owned_http_client = httpx.AsyncClient()
            self.http_client = self._owned_http_client
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._owned_http_client:
            await self._owned_http_client.aclose()

    def _get_headers(self) -> Dict[str, str]:
        headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "git-sniff-cli"
        }
        if self.token:
            headers["Authorization"] = f"token {self.token}"
        return headers

    def _update_rate_limits(self, response_headers: httpx.Headers):
        remaining = response_headers.get("X-RateLimit-Remaining")
        reset = response_headers.get("X-RateLimit-Reset")
        if remaining is not None:
            try:
                self.rate_limit_remaining = int(remaining)
            except ValueError:
                pass
        if reset is not None:
            try:
                self.rate_limit_reset = int(reset)
            except ValueError:
                pass

    def get_rate_limit_warning(self) -> Optional[str]:
        """
        Surfaces warning if remaining call count drops below 10.
        """
        if self.rate_limit_remaining is not None and self.rate_limit_remaining < 10:
            return f"[🟡 WARNING] Rate limit critically low ({self.rate_limit_remaining} calls remaining). Please supply GITHUB_TOKEN."
        return None

    async def _get(self, url: str, params: Optional[Dict[str, Any]] = None) -> httpx.Response:
        """Sends a GET request, extracting rate limit headers and validating errors."""
        response = await self.http_client.get(
            url,
            headers=self._get_headers(),
            params=params,
            timeout=15.0
        )
        self._update_rate_limits(response.headers)
        return response

    async def fetch_repo_metadata(self, owner: str, repo: str) -> Dict[str, Any]:
        """
        Fetches repository metadata to retrieve stargazers count, open issues, and default branch.
        """
        url = f"https://api.github.com/repos/{owner}/{repo}"
        response = await self._get(url)
        if response.status_code == 404:
            raise ValueError(f"Repository {owner}/{repo} not found or is private.")
        response.raise_for_status()
        return response.json()

    async def fetch_issues(self, owner: str, repo: str) -> List[Dict[str, Any]]:
        """
        Fetches the 100 most recent issues/PRs.
        """
        url = f"https://api.github.com/repos/{owner}/{repo}/issues"
        params = {"state": "all", "per_page": 100}
        try:
            response = await self._get(url, params=params)
            if response.status_code == 200:
                return response.json()
        except Exception as e:
            logger.warning(f"Failed to fetch issues: {e}")
        return []

    async def fetch_file_tree(self, owner: str, repo: str, branch: str) -> List[str]:
        """
        Performs recursive directory scan in a single network round-trip.
        """
        url = f"https://api.github.com/repos/{owner}/{repo}/git/trees/{branch}"
        params = {"recursive": "1"}
        try:
            response = await self._get(url, params=params)
            if response.status_code == 200:
                tree = response.json().get("tree", [])
                return [item["path"] for item in tree if isinstance(item, dict) and "path" in item]
        except Exception as e:
            logger.warning(f"Failed to fetch file tree: {e}")
        return []

    async def fetch_commit_status(self, owner: str, repo: str, branch: str) -> str:
        """
        Retrieves the combined status checks and check suites for the latest commit.
        """
        status_state = "none"
        try:
            # 1. Fetch Classic Commits Status
            status_url = f"https://api.github.com/repos/{owner}/{repo}/commits/{branch}/status"
            status_res = await self._get(status_url)
            if status_res.status_code == 200:
                classic_state = status_res.json().get("state", "none").lower()
                if classic_state in ("success", "pending", "failure"):
                    status_state = classic_state
        except Exception as e:
            logger.warning(f"Failed to fetch classic commit status: {e}")

        try:
            # 2. Fetch Check Suites (Modern GitHub Actions)
            suites_url = f"https://api.github.com/repos/{owner}/{repo}/commits/{branch}/check-suites"
            suites_res = await self._get(suites_url)
            if suites_res.status_code == 200:
                suites = suites_res.json().get("check_suites", [])
                # If any check suite failed, mark as failure. If any in progress, pending.
                has_failure = False
                has_pending = False
                has_success = False
                
                for suite in suites:
                    conclusion = suite.get("conclusion")
                    status = suite.get("status")
                    if conclusion == "failure":
                        has_failure = True
                    elif status in ("queued", "in_progress"):
                        has_pending = True
                    elif conclusion == "success":
                        has_success = True
                
                if has_failure:
                    status_state = "failure"
                elif has_pending and status_state != "failure":
                    status_state = "pending"
                elif has_success and status_state not in ("failure", "pending"):
                    status_state = "success"
        except Exception as e:
            logger.warning(f"Failed to fetch check suites status: {e}")

        return status_state

    async def fetch_commits(self, owner: str, repo: str, per_page: int = 50) -> List[Dict[str, Any]]:
        """
        Fetches a list of recent commits.
        """
        url = f"https://api.github.com/repos/{owner}/{repo}/commits"
        params = {"per_page": per_page}
        try:
            response = await self._get(url, params=params)
            if response.status_code == 200:
                return response.json()
        except Exception as e:
            logger.warning(f"Failed to fetch commits: {e}")
        return []

    async def fetch_contributors(self, owner: str, repo: str) -> List[Dict[str, Any]]:
        """
        Fetches contributor stats, implementing a non-blocking 202 Accepted retry loop.
        """
        url = f"https://api.github.com/repos/{owner}/{repo}/stats/contributors"
        for attempt in range(2):
            try:
                response = await self._get(url)
                if response.status_code == 200:
                    return response.json()
                elif response.status_code == 202:
                    logger.info("GitHub API returned 202 Accepted. Retrying stats compilation in 1.5s...")
                    await asyncio.sleep(1.5)
                else:
                    break
            except Exception as e:
                logger.warning(f"Failed to fetch contributor stats: {e}")
                break
        return []

    async def fetch_file_content(self, owner: str, repo: str, branch: str, path: str) -> Optional[str]:
        """
        Fetches content of a manifest file. Fallbacks to raw downloads for oversized items.
        """
        url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}"
        params = {"ref": branch}
        try:
            response = await self._get(url, params=params)
            if response.status_code == 200:
                data = response.json()
                content_b64 = data.get("content", "")
                content_bytes = base64.b64decode(content_b64.encode("utf-8"))
                return content_bytes.decode("utf-8", errors="ignore")
        except Exception as e:
            logger.warning(f"Failed to fetch {path} via API contents: {e}")

        # Fallback to Raw content downloads
        raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{path}"
        try:
            response = await self.http_client.get(raw_url, timeout=10.0)
            if response.status_code == 200:
                return response.text
        except Exception as e:
            logger.warning(f"Failed to fetch raw {path}: {e}")
        return None

    async def calculate_dependencies_count(self, owner: str, repo: str, branch: str, file_paths: List[str]) -> Tuple[int, bool]:
        """
        Locates manifest files (pyproject.toml, package.json, Cargo.toml) in the root path,
        downloads and parses their dependencies, and checks for pyproject linting configurations.
        """
        deps_count = 0
        pyproject_linting = False

        # Look specifically for root-level manifest files
        manifests = [p for p in file_paths if p in ("package.json", "pyproject.toml", "Cargo.toml")]

        for path in manifests:
            content = await self.fetch_file_content(owner, repo, branch, path)
            if not content:
                continue

            try:
                if path == "package.json":
                    parsed = json.loads(content)
                    deps = parsed.get("dependencies", {})
                    dev_deps = parsed.get("devDependencies", {})
                    deps_count += len(deps) + len(dev_deps)
                elif path == "pyproject.toml":
                    parsed = tomllib.loads(content)
                    deps_count += count_pyproject_deps(parsed)
                    pyproject_linting = check_pyproject_linting(parsed)
                elif path == "Cargo.toml":
                    parsed = tomllib.loads(content)
                    deps_count += count_cargo_deps(parsed)
            except Exception as e:
                logger.warning(f"Error parsing manifest file {path}: {e}")

        return deps_count, pyproject_linting
