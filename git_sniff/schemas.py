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
