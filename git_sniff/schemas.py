from pydantic import BaseModel
from typing import Optional

class PillarScores(BaseModel):
    maintenance: int
    cicd: int
    dependencies: int
    bus_factor: int

class RepoScorecard(BaseModel):
    repository: str
    overall_score: int
    status: str  # "HEALTHY", "WARNING", "CRITICAL"
    breakdown: PillarScores
    recommendation: str
    calculated_at: str
    rate_limit_warning: Optional[str] = None


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
