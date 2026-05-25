---
name: Repo Hygiene Scorecard
description: This skill should be used when the user asks to "check the code hygiene of a repo", "is this GitHub repo worth cloning/forking", "should I depend on this library", "evaluate this repo before cloning", "what's the bus factor of", "is this repo well maintained", or shares a GitHub repo URL and asks whether it is healthy, maintained, or safe to build on. Produces a 0–100 git-sniff scorecard with maintenance, CI/CD, dependency, and bus-factor pillars.
version: 0.1.0
---

# Repo Hygiene Scorecard

## Purpose

Evaluate the health of a public GitHub repository before cloning, forking, or
taking on a dependency. Wraps the local **git-sniff** microservice to produce an
overall score (0–100), a status (HEALTHY / WARNING / CRITICAL), four pillar
scores, and a recommendation. This turns a vague "is this repo any good?" into a
concrete, weighted, reproducible scorecard — useful before `git clone`, before
forking, or before adding a library to a project.

## Workflow

### 1. Resolve the repository identifier

Extract `owner/repo` from the request. Accept either the bare slug
(`NACLab/ngc-learn`) or a GitHub URL
(`https://github.com/NACLab/ngc-learn` → `NACLab/ngc-learn`). Strip any trailing
path, `.git`, query string, or fragment.

### 2. Fetch the scorecard

Run the bundled script, which reuses a running git-sniff server or starts one,
then returns the JSON scorecard:

```bash
bash "${CLAUDE_PLUGIN_ROOT}/skills/repo-hygiene/scripts/sniff.sh" <owner>/<repo>
```

The script ensures the backend (default port 8000) is healthy (starting
`git-sniff --server` in the background if needed), then issues
`GET /sniff?repo=<owner>/<repo>` and prints the JSON. It honors
`GITHUB_PERSONAL_ACCESS_TOKEN` from the environment to avoid GitHub's
60-requests/hour unauthenticated cap.

If the script reports `git-sniff not installed` (exit 3), install the package
first (`pip install -e .` from the git-sniff repo), then retry.

### 3. Present the scorecard

Parse the JSON and present a compact scorecard. Lead with the overall score and
status, then the four pillars, then the recommendation. Map scores to a signal:
**≥80 green, 50–79 yellow, <50 red**. Example shape:

```
owner/repo — 59/100 (WARNING)
  Maintenance   100   Median issue resolution fast
  CI/CD          55   Workflows present, checks mixed
  Dependencies    0   No Dependabot/Renovate detected
  Bus factor     60   Moderate concentration
→ Use with caution. Minor gaps in engineering rigor or dependency management.
```

Use `recommendation` from the payload as the closing line. Surface
`rate_limit_warning` if present.

### 4. Interpret on request

When the user asks *why* a pillar scored as it did, or what a number means,
consult `references/scoring.md` for the exact weights, boundaries, and pillar
definitions — explain in those terms rather than guessing.

## Error handling

Map the script's stderr / exit codes and the JSON `detail` field to clear advice:

- **Exit 2 / HTTP 400** — malformed `owner/repo`. Re-extract the slug.
- **HTTP 404** — repository not found or private. git-sniff only evaluates
  public repos.
- **HTTP 403** — GitHub rate limit. Advise setting `GITHUB_PERSONAL_ACCESS_TOKEN`
  (a zero-scope classic PAT suffices for public read), then retry.
- **Exit 3** — backend unavailable / not installed (see step 2).
- **Exit 4 / HTTP 500** — engine error; report the `detail` message.

## Comparing multiple repositories

When asked to compare repos (e.g. choosing between alternatives), fetch each via
the script and present a single ranked table sorted by overall score, with the
pillar columns alongside so trade-offs are visible (e.g. one repo strong on
maintenance but weak on bus factor).

## Additional resources

- **`scripts/sniff.sh`** — ensures the backend is up and returns the JSON
  scorecard for one repo. Exit codes: 0 ok, 2 bad args, 3 server unavailable,
  4 request failed.
- **`references/scoring.md`** — pillar weights, score boundaries, status
  thresholds, and the JSON response shape. Load when interpreting or explaining
  a score.
