# git-sniff scoring model

How the overall score, status, and four pillar scores are derived. Use this to
interpret and explain a scorecard without reading the engine source.

## Overall score and status

Overall is a weighted average of the four pillars (each 0–100), rounded:

| Pillar | Weight |
|---|---|
| Maintenance Vitality | 30% |
| CI/CD & Engineering Rigor | 25% |
| Dependency Hygiene | 20% |
| Bus Factor Sustainability | 25% |

Status thresholds on the overall score:

| Overall | Status | Meaning |
|---|---|---|
| ≥ 80 | HEALTHY | Production-ready; high velocity and modern tooling. |
| 50–79 | WARNING | Use with caution; gaps in rigor or dependency management. |
| < 50 | CRITICAL | High risk; weak maintenance, CI/CD, or severe bus-factor risk. |

## Pillar 1 — Maintenance Vitality (30%)

Based on **median time to resolution (MTR)** of closed issues:

| MTR (days) | Score |
|---|---|
| ≤ 7 | 100 |
| ≤ 30 | 80 |
| ≤ 90 | 50 |
| > 90 | 10 |

- No issues at all → 100 (clean baseline).
- Issues exist but none closed → 50.
- **Stagnation penalty** −15 when `stars > 1000` and `open_issues / stars > 0.15`.

## Pillar 2 — CI/CD & Engineering Rigor (25%)

Additive, capped at 100:

- **+40** `.github/workflows/` present.
- **+30** linter/format config present (`.pre-commit-config.yaml`, `.markdownlint.json`, any `eslintrc`, or a `pyproject.toml` with a `[tool.{black,ruff,flake8,isort,pylint,mypy}]` section).
- **Status checks** on the default branch: success **+30**, pending **+15**, none/failure **+0**.

## Pillar 3 — Dependency Hygiene (20%)

Additive partial credit over deterministic repository-state signals, capped at 100:

| Signal | Points |
|---|---|
| Dependency manifest declared (`package.json`, `pyproject.toml`, `Cargo.toml`, `go.mod`, `requirements*.txt`, `setup.py/cfg`, `Pipfile`, `Gemfile`, `pom.xml`, `build.gradle`, `composer.json`, …) | +20 |
| Lockfile present (`package-lock.json`, `yarn.lock`, `pnpm-lock.yaml`, `poetry.lock`, `uv.lock`, `Cargo.lock`, `go.sum`, `composer.lock`, `flake.lock`, …) | +30 |
| Leanness by declared count: ≤10 → +30, ≤25 → +20, ≤50 → +10, >50 → +0; count not parseable → neutral +15 | +0–30 |
| Automated updates: a Dependabot/Renovate **config file** in the tree, **or** ≥10% of the last 50 commits bot-authored | +20 |

Rationale: the prior model awarded 100 only when Dependabot/Renovate authored
≥10% of the last 50 commits, else 0. Empirically that returned 0 for ~80% of
actively-developed repos (corporate and academic alike) because active human
development pushes bot commits out of the recent window — it measured commit
timing, not hygiene. Automation is now detected from the **config file** (stable)
rather than the commit window (timing-sensitive), and manifest/lockfile/leanness
give partial credit so a clean repo without bots is no longer scored zero.

## Pillar 4 — Bus Factor Sustainability (25%)

Velocity concentration across contributors (top contributor's share of total contributions):

| Condition | Score |
|---|---|
| No active contributors | 50 |
| Exactly 1 contributor | 20 |
| Top share > 85% | 20 (critical single point of failure) |
| Top share 60–85% | 60 (moderate risk) |
| Top share < 50% and ≥ 3 contributors | 100 (highly sustainable) |
| Otherwise (top share < 60%) | 80 (moderate distribution) |

Note: when GitHub's `/stats/contributors` endpoint is still compiling (HTTP 202),
the engine falls back to commit-authorship counts, which can shift the bus-factor
score slightly versus a warm run. The score is otherwise deterministic.

## Response shape

`GET /sniff?repo=owner/repo` returns:

```json
{
  "repository": "owner/repo",
  "overall_score": 59,
  "status": "WARNING",
  "breakdown": { "maintenance": 100, "cicd": 55, "dependencies": 0, "bus_factor": 60 },
  "recommendation": "Use with caution. Minor gaps in engineering rigor or dependency management.",
  "calculated_at": "2026-05-20T12:00:00Z",
  "rate_limit_warning": null
}
```

Error responses use `{"detail": "..."}` with HTTP 400 (bad repo format),
404 (repo not found/private), 403 (GitHub rate limit — set `GITHUB_TOKEN`),
or 500 (engine error).
