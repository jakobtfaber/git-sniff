import statistics
from datetime import datetime
from typing import Optional, Tuple, List, Dict, Any

def parse_datetime(dt_str: str) -> datetime:
    """
    Parses ISO 8601 timestamps, handling the trailing 'Z' character
    consistently across all Python versions.
    """
    if dt_str.endswith("Z"):
        dt_str = dt_str[:-1] + "+00:00"
    return datetime.fromisoformat(dt_str)

def check_pyproject_linting(toml_dict: Dict[str, Any]) -> bool:
    """Checks if pyproject.toml contains standard linting sections."""
    tool = toml_dict.get("tool", {})
    if not isinstance(tool, dict):
        return False
    lint_tools = {"black", "ruff", "flake8", "isort", "pylint", "mypy"}
    for t in tool.keys():
        if t.lower() in lint_tools:
            return True
    return False

def count_pyproject_deps(toml_dict: Dict[str, Any]) -> int:
    """Counts root dependencies in a pyproject.toml file."""
    count = 0
    project = toml_dict.get("project", {})
    if isinstance(project, dict):
        deps = project.get("dependencies", [])
        if isinstance(deps, list):
            count += len(deps)
        opt_deps = project.get("optional-dependencies", {})
        if isinstance(opt_deps, dict):
            for group_deps in opt_deps.values():
                if isinstance(group_deps, list):
                    count += len(group_deps)
    
    tool = toml_dict.get("tool", {})
    if isinstance(tool, dict):
        poetry = tool.get("poetry", {})
        if isinstance(poetry, dict):
            poetry_deps = poetry.get("dependencies", {})
            if isinstance(poetry_deps, dict):
                count += len(poetry_deps)
            group = poetry.get("group", {})
            if isinstance(group, dict):
                for g_val in group.values():
                    if isinstance(g_val, dict):
                        g_deps = g_val.get("dependencies", {})
                        if isinstance(g_deps, dict):
                            count += len(g_deps)
            dev_deps = poetry.get("dev-dependencies", {})
            if isinstance(dev_deps, dict):
                count += len(dev_deps)
    return count

def count_cargo_deps(toml_dict: Dict[str, Any]) -> int:
    """Counts dependencies in a Cargo.toml file."""
    count = 0
    for sec in ["dependencies", "dev-dependencies", "build-dependencies"]:
        deps = toml_dict.get(sec, {})
        if isinstance(deps, dict):
            count += len(deps)
    return count

def calculate_maintenance_score(
    issues_data: List[Dict[str, Any]],
    stars_count: int,
    open_issues_count: int
) -> Tuple[int, str]:
    """
    Pillar 1: Maintenance Vitality (Weight: 30%)
    Calculates score based on Median Time to Resolution (MTR) for closed issues.
    Applies stagnation penalty if open_issues/stars > 0.15 and stars > 1000.
    """
    if not issues_data:
        return 100, "No issues found. Perfect baseline health."

    closed_issues = [i for i in issues_data if i.get("state") == "closed" and i.get("closed_at") and i.get("created_at")]
    
    if not closed_issues:
        # >0 issues but 0 closed -> default warning baseline
        score = 50
        desc = "0 closed issues found (all active issues are unresolved)."
    else:
        # Calculate days to resolution for each closed issue
        resolutions = []
        for issue in closed_issues:
            created = parse_datetime(issue["created_at"])
            closed = parse_datetime(issue["closed_at"])
            days = (closed - created).total_seconds() / 86400.0
            resolutions.append(max(0.0, days))
        
        mtr = statistics.median(resolutions)
        
        # MTR Score Boundaries
        if mtr <= 7.0:
            score = 100
        elif mtr <= 30.0:
            score = 80
        elif mtr <= 90.0:
            score = 50
        else:
            score = 10
        
        desc = f"Median resolution time: {mtr:.1f} days ({len(closed_issues)} closed issues)"

    # Stagnation Penalty check
    if stars_count > 1000 and (open_issues_count / stars_count) > 0.15:
        score -= 15
        desc += " [Stagnation Penalty: High open issues to stars ratio]"
        
    # Clamp score
    score = max(0, min(100, score))
    return score, desc

def calculate_cicd_score(
    file_paths: List[str],
    commit_status_state: str,
    pyproject_has_linting: bool = False
) -> Tuple[int, str]:
    """
    Pillar 2: CI/CD & Rigor Compliance (Weight: 25%)
    Checks files recursively for .github/workflows (+40), standard configuration formats (+30),
    and validates the status check state (+30).
    """
    # 1. Check for workflows directory
    has_workflows = any(p.startswith(".github/workflows/") or p == ".github/workflows" for p in file_paths)
    workflows_score = 40 if has_workflows else 0

    # 2. Check for configuration files
    lint_configs = {".pre-commit-config.yaml", ".markdownlint.json"}
    has_lint_config = any(
        p in lint_configs or 
        "eslintrc" in p or 
        (p == "pyproject.toml" and pyproject_has_linting)
        for p in file_paths
    )
    lint_score = 30 if has_lint_config else 0

    # 3. Status checks scoring
    status_state = commit_status_state.lower()
    if status_state == "success":
        status_score = 30
    elif status_state == "pending":
        status_score = 15
    else:
        status_score = 0

    score = workflows_score + lint_score + status_score
    score = max(0, min(100, score))

    details = []
    if has_workflows:
        details.append(".github workflows found")
    else:
        details.append(".github workflows missing")
        
    if has_lint_config:
        details.append("pre-commit/linters active")
    else:
        details.append("no linter config found")

    details.append(f"commit checks: {commit_status_state}")

    return score, ", ".join(details)

# Reproducible-install lockfiles across ecosystems (matched by basename).
LOCKFILES = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "npm-shrinkwrap.json",
    "poetry.lock", "pipfile.lock", "uv.lock", "pdm.lock", "conda-lock.yml",
    "cargo.lock", "go.sum", "gemfile.lock", "composer.lock", "flake.lock",
}
# Declared-dependency manifests (matched by basename; requirements*.txt handled separately).
MANIFESTS = {
    "package.json", "pyproject.toml", "cargo.toml", "go.mod", "setup.py",
    "setup.cfg", "environment.yml", "environment.yaml", "pipfile", "gemfile",
    "pom.xml", "build.gradle", "composer.json",
}
# Automated dependency-update tool configs (matched by basename, e.g. .github/dependabot.yml).
AUTOMATION_CONFIGS = {
    "dependabot.yml", "dependabot.yaml", "renovate.json", "renovate.json5",
    ".renovaterc", ".renovaterc.json",
}

def _recent_bot_share(commits_data: List[Dict[str, Any]]) -> float:
    """Share of the last 50 commits authored by Dependabot/Renovate."""
    recent_commits = commits_data[:50]
    sample_size = len(recent_commits)
    if sample_size == 0:
        return 0.0
    bot_commits = 0
    for c in recent_commits:
        author_login = ((c.get("author") or {}).get("login") or "").lower()
        author_name = (((c.get("commit") or {}).get("author") or {}).get("name") or "").lower()
        if "dependabot" in author_login or "dependabot" in author_name \
                or "renovate" in author_login or "renovate" in author_name:
            bot_commits += 1
    return bot_commits / sample_size

def calculate_dependency_score(
    commits_data: List[Dict[str, Any]],
    dependencies_count: int,
    file_paths: Optional[List[str]] = None
) -> Tuple[int, str]:
    """
    Pillar 3: Dependency Hygiene (Weight: 15%)

    Additive partial-credit model over deterministic, repository-state signals
    (capped at 100), replacing the prior all-or-nothing bot-commit proxy that
    returned 0 for ~80% of actively-developed repos regardless of ownership:
      - Manifest declared            +20
      - Lockfile present             +30
      - Leanness (by declared count) +0..30 (neutral +15 when count unknown)
      - Automated updates            +20 (Dependabot/Renovate config file, or
                                          >=10% recent bot-authored commits)
    Automation is detected primarily from the config file in the tree, which is
    stable, rather than from the commit window, which is timing-sensitive.
    """
    file_paths = file_paths or []
    basenames = {p.rsplit("/", 1)[-1].lower() for p in file_paths}

    has_manifest = bool(basenames & MANIFESTS) or any(
        b.startswith("requirements") and b.endswith(".txt") for b in basenames
    )
    has_lockfile = bool(basenames & LOCKFILES)
    has_automation_config = bool(basenames & AUTOMATION_CONFIGS)
    bot_share = _recent_bot_share(commits_data)
    has_bot_activity = bot_share >= 0.10

    score = 0
    parts: List[str] = []

    if has_manifest:
        score += 20
        parts.append("manifest declared")
    else:
        parts.append("no dependency manifest found")

    if has_lockfile:
        score += 30
        parts.append("lockfile present")
    else:
        parts.append("no lockfile (non-reproducible installs)")

    if dependencies_count > 0:
        if dependencies_count <= 10:
            score += 30
            parts.append(f"lean ({dependencies_count} deps)")
        elif dependencies_count <= 25:
            score += 20
            parts.append(f"moderate ({dependencies_count} deps)")
        elif dependencies_count <= 50:
            score += 10
            parts.append(f"heavy ({dependencies_count} deps)")
        else:
            parts.append(f"bloated ({dependencies_count} deps)")
    else:
        score += 15
        parts.append("dependency count not assessable")

    if has_automation_config or has_bot_activity:
        score += 20
        if has_automation_config:
            parts.append("Dependabot/Renovate configured")
        else:
            parts.append(f"automated updates active (bot share {bot_share * 100:.0f}%)")
    else:
        parts.append("no automated dependency updates")

    score = max(0, min(100, score))
    return score, ", ".join(parts)

def calculate_bus_factor_score(
    contributors_data: List[Dict[str, Any]]
) -> Tuple[int, str]:
    """
    Pillar 4: The Bus Factor / Sustenance Risk (Weight: 30%)
    Identifies velocity concentration. Uses a watertight non-overlapping decision tree
    with an 80 pts fallback to handle all distribution configurations.
    """
    # Extract contribution numbers
    velocities = []
    for c in contributors_data:
        val = c.get("contributions") or c.get("total") or 0
        if val > 0:
            velocities.append(val)
            
    if not velocities:
        return 50, "No active contributors in the last year."
        
    if len(velocities) == 1:
        return 20, "Single point of failure (1 active contributor)."

    # Sort descending
    velocities.sort(reverse=True)
    total_val = sum(velocities)
    top_share = velocities[0] / total_val

    if top_share > 0.85:
        score = 20
        desc = f"Critical single point of failure (top contributor controls {top_share * 100:.1f}% velocity)"
    elif 0.60 <= top_share <= 0.85:
        score = 60
        desc = f"Moderate risk (top contributor controls {top_share * 100:.1f}% velocity)"
    else:  # top_share < 0.60
        # Check highly sustainable criteria
        if len(velocities) >= 3 and top_share < 0.50:
            score = 100
            desc = f"Highly sustainable (distributed among {len(velocities)} core maintainers)"
        else:
            score = 80
            desc = f"Moderate distribution (velocity share is {top_share * 100:.1f}% among {len(velocities)} core maintainers)"

    return score, desc

def calculate_overall_score(
    maintenance: int,
    cicd: int,
    dependencies: int,
    bus_factor: int
) -> Tuple[int, str, str]:
    """
    Aggregates scores across the four pillars and evaluates recommendation.
    """
    weighted_score = (
        0.30 * maintenance +
        0.25 * cicd +
        0.15 * dependencies +
        0.30 * bus_factor
    )
    score = max(0, min(100, round(weighted_score)))
    
    if score >= 80:
        status = "HEALTHY"
        rec = "Production ready. High contribution velocity and modern tooling."
    elif score >= 50:
        status = "WARNING"
        rec = "Use with caution. Minor gaps in engineering rigor or dependency management."
    else:
        status = "CRITICAL"
        rec = "High risk. Lacks essential maintenance, CI/CD, or has severe Bus Factor risks."
        
    return score, status, rec
