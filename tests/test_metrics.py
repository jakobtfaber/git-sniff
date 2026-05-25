import pytest
from datetime import datetime, timedelta
from git_sniff.metrics import (
    calculate_maintenance_score,
    calculate_cicd_score,
    calculate_dependency_score,
    calculate_bus_factor_score,
    calculate_overall_score,
    parse_datetime
)

# ==============================================================================
# Helper ISO Parsing Tests
# ==============================================================================

def test_parse_datetime_iso():
    # Test trailing 'Z' compatibility
    dt1 = parse_datetime("2026-05-24T16:00:22Z")
    assert dt1.year == 2026
    assert dt1.month == 5
    assert dt1.day == 24
    assert dt1.hour == 16

    # Test standard ISO compatibility
    dt2 = parse_datetime("2026-05-24T16:00:22+00:00")
    assert dt2.year == 2026
    assert dt2.month == 5
    assert dt2.day == 24
    assert dt2.hour == 16


# ==============================================================================
# Pillar 1: Maintenance Vitality Tests
# ==============================================================================

def test_maintenance_empty_sets():
    # 0 issues -> default score 100
    score, desc = calculate_maintenance_score([], stars_count=500, open_issues_count=0)
    assert score == 100
    assert "No issues found" in desc

    # >0 issues but 0 closed -> default score 50
    issues = [
        {"state": "open", "created_at": "2026-05-20T12:00:00Z"}
    ]
    score, desc = calculate_maintenance_score(issues, stars_count=500, open_issues_count=1)
    assert score == 50
    assert "0 closed" in desc or "unresolved" in desc.lower()


def test_maintenance_mtr_thresholds():
    # Under 7 days -> 100 points
    now = datetime(2026, 5, 24, 12, 0, 0)
    issues_under_7 = [
        {
            "state": "closed",
            "created_at": (now - timedelta(days=5)).isoformat() + "Z",
            "closed_at": now.isoformat() + "Z"
        }
    ]
    score, _ = calculate_maintenance_score(issues_under_7, stars_count=100, open_issues_count=0)
    assert score == 100

    # 7 to 30 days -> 80 points
    issues_7_to_30 = [
        {
            "state": "closed",
            "created_at": (now - timedelta(days=15)).isoformat() + "Z",
            "closed_at": now.isoformat() + "Z"
        }
    ]
    score, _ = calculate_maintenance_score(issues_7_to_30, stars_count=100, open_issues_count=0)
    assert score == 80

    # 30 to 90 days -> 50 points
    issues_30_to_90 = [
        {
            "state": "closed",
            "created_at": (now - timedelta(days=45)).isoformat() + "Z",
            "closed_at": now.isoformat() + "Z"
        }
    ]
    score, _ = calculate_maintenance_score(issues_30_to_90, stars_count=100, open_issues_count=0)
    assert score == 50

    # Over 90 days -> 10 points
    issues_over_90 = [
        {
            "state": "closed",
            "created_at": (now - timedelta(days=120)).isoformat() + "Z",
            "closed_at": now.isoformat() + "Z"
        }
    ]
    score, _ = calculate_maintenance_score(issues_over_90, stars_count=100, open_issues_count=0)
    assert score == 10


def test_maintenance_stagnation_penalty():
    # Stagnation penalty check:
    # open_issues / stars > 0.15 and stars > 1000 -> -15 points penalty
    now = datetime(2026, 5, 24, 12, 0, 0)
    issues = [
        {
            "state": "closed",
            "created_at": (now - timedelta(days=5)).isoformat() + "Z",
            "closed_at": now.isoformat() + "Z"
        }
    ]
    # Under 7 days (100 pts) - 15 stagnation penalty = 85 pts
    score, desc = calculate_maintenance_score(issues, stars_count=2000, open_issues_count=400)
    assert score == 85
    assert "stagnation" in desc.lower()

    # If stars <= 1000, no penalty
    score_no_penalty, _ = calculate_maintenance_score(issues, stars_count=900, open_issues_count=300)
    assert score_no_penalty == 100

    # If ratio <= 0.15, no penalty
    score_low_ratio, _ = calculate_maintenance_score(issues, stars_count=2000, open_issues_count=100)
    assert score_low_ratio == 100


# ==============================================================================
# Pillar 2: CI/CD & Engineering Rigor Tests
# ==============================================================================

def test_cicd_score():
    # Perfect score: workflows directory (+40), pre-commit configuration (+30), and commit status success (+30) = 100
    score, desc = calculate_cicd_score(
        file_paths=[".github/workflows/ci.yml", ".pre-commit-config.yaml", "README.md"],
        commit_status_state="success"
    )
    assert score == 100
    assert "workflows" in desc.lower()
    assert "pre-commit" in desc.lower() or "linters" in desc.lower()

    # workflows missing, lint active, pending status (30 + 15 = 45)
    score, _ = calculate_cicd_score(
        file_paths=[".eslintrc.json", "package.json"],
        commit_status_state="pending"
    )
    assert score == 45

    # pyproject.toml with linting sections active (+30)
    score, _ = calculate_cicd_score(
        file_paths=[".github/workflows/deploy.yml", "pyproject.toml"],
        commit_status_state="failure",
        pyproject_has_linting=True
    )
    assert score == 70  # 40 (workflows) + 30 (pyproject linter) + 0 (status failure) = 70


# ==============================================================================
# Pillar 3: Dependency Hygiene Tests
# ==============================================================================

def test_dependency_full_hygiene():
    # manifest +20, lockfile +30, lean (<=10) +30, bot activity +20 -> 100
    commits = [{"author": {"login": "dependabot[bot]"}, "commit": {"author": {"name": "dependabot[bot]"}}}]
    score, desc = calculate_dependency_score(
        commits, dependencies_count=8, file_paths=["package.json", "package-lock.json"]
    )
    assert score == 100
    assert "lockfile present" in desc
    assert "lean" in desc


def test_dependency_no_automation_is_not_zero():
    # The core regression: a clean repo with no Dependabot must NOT score 0.
    # manifest +20, lockfile +30, lean +30, no automation +0 -> 80
    score, desc = calculate_dependency_score(
        [], dependencies_count=8, file_paths=["pyproject.toml", "poetry.lock"]
    )
    assert score == 80
    assert "no automated dependency updates" in desc


def test_dependency_automation_via_config_file():
    # Dependabot config in the tree counts even with zero recent bot commits
    # (the timing-window fix). manifest +20, lockfile +30, lean +30, config +20 -> 100
    score, desc = calculate_dependency_score(
        [], dependencies_count=5,
        file_paths=["package.json", "package-lock.json", ".github/dependabot.yml"]
    )
    assert score == 100
    assert "Dependabot/Renovate configured" in desc


def test_dependency_bloat_and_no_lockfile():
    # manifest +20, no lockfile +0, bloated (>50) +0, no automation +0 -> 20
    score, desc = calculate_dependency_score(
        [], dependencies_count=60, file_paths=["package.json"]
    )
    assert score == 20
    assert "bloated" in desc
    assert "no lockfile" in desc


def test_dependency_unknown_count_neutral():
    # go.mod has no parseable count in the engine -> neutral leanness +15.
    # manifest +20, lockfile +30 (go.sum), neutral +15, no automation +0 -> 65
    score, desc = calculate_dependency_score(
        [], dependencies_count=0, file_paths=["go.mod", "go.sum"]
    )
    assert score == 65
    assert "not assessable" in desc


# ==============================================================================
# Pillar 4: Bus Factor & Sustenance Risk Tests
# ==============================================================================

def test_bus_factor_single_point_of_failure():
    # Only 1 contributor -> 20 points
    score, desc = calculate_bus_factor_score([{"login": "lead", "contributions": 150}])
    assert score == 20
    assert "single point of failure" in desc.lower()

    # Top contributor has > 85% share -> 20 points
    contribs = [
        {"login": "lead", "contributions": 90},
        {"login": "helper", "contributions": 5},
        {"login": "helper2", "contributions": 5}
    ]
    score, _ = calculate_bus_factor_score(contribs)
    assert score == 20


def test_bus_factor_moderate_risk():
    # Top contributor has 60% - 85% share -> 60 points
    contribs = [
        {"login": "lead", "contributions": 70},
        {"login": "helper1", "contributions": 15},
        {"login": "helper2", "contributions": 15}
    ]
    score, desc = calculate_bus_factor_score(contribs)
    assert score == 60
    assert "moderate risk" in desc.lower()


def test_bus_factor_distributed_sustainable():
    # top contributor < 50% and >= 3 contributors each < 50% -> 100 points
    contribs = [
        {"login": "dev1", "contributions": 40},
        {"login": "dev2", "contributions": 35},
        {"login": "dev3", "contributions": 25}
    ]
    score, desc = calculate_bus_factor_score(contribs)
    assert score == 100
    assert "sustainable" in desc.lower()


def test_bus_factor_fallback_scenarios():
    # Case A: Top share is 55% (<60%), but there are only 2 contributors -> returns 80 points
    contribs_2_bal = [
        {"login": "dev1", "contributions": 55},
        {"login": "dev2", "contributions": 45}
    ]
    score, desc = calculate_bus_factor_score(contribs_2_bal)
    assert score == 80
    assert "balanced" in desc.lower() or "moderate distribution" in desc.lower()

    # Case B: Top share is 45% (<50%), but only 2 contributors -> returns 80 points
    contribs_2_low = [
        {"login": "dev1", "contributions": 45},
        {"login": "dev2", "contributions": 40}
    ]
    score, _ = calculate_bus_factor_score(contribs_2_low)
    assert score == 80

    # Case C: Empty contributors -> 50 points neutral default
    score, desc = calculate_bus_factor_score([])
    assert score == 50
    assert "no active contributors" in desc.lower()


# ==============================================================================
# Overall Score Calculation Tests
# ==============================================================================

def test_overall_score_calculation():
    # Overall score = 30% * M + 25% * C + 20% * D + 25% * B
    # e.g., M=90, C=100, D=75, B=85
    # Expected: 0.30*90 + 0.25*100 + 0.20*75 + 0.25*85 = 27 + 25 + 15 + 21.25 = 88.25 -> rounded to 88
    score, status, rec = calculate_overall_score(
        maintenance=90,
        cicd=100,
        dependencies=75,
        bus_factor=85
    )
    assert score == 88
    assert status == "HEALTHY"
    assert "production ready" in rec.lower()

    # Test WARNING boundaries (score 50-79)
    # M=60, C=60, D=60, B=60 -> 60 points -> WARNING
    score, status, _ = calculate_overall_score(60, 60, 60, 60)
    assert score == 60
    assert status == "WARNING"

    # Test CRITICAL boundaries (<50)
    # M=30, C=30, D=30, B=30 -> 30 points -> CRITICAL
    score, status, _ = calculate_overall_score(30, 30, 30, 30)
    assert score == 30
    assert status == "CRITICAL"
