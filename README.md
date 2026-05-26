# git-sniff

An instant architectural, maintenance, and compliance health scorecard for public GitHub repositories. Query repo stats in your terminal before cloning, or run the local background microservice to support web-extension querying.

---

## Installation

1. Clone or navigate to the directory and install in editable mode:
   ```bash
   pip install -e .
   ```

   To install development dependencies (like `pytest`):
   ```bash
   pip install -e ".[dev]"
   ```

2. (Optional but Recommended) Set your GitHub Token to bypass rate limits:
   ```bash
   export GITHUB_PERSONAL_ACCESS_TOKEN="your_personal_access_token"
   ```

---

## Usage

### 1. CLI Mode
Check any public repository instantly in your terminal:
```bash
git-sniff langchain-ai/deepagents
```

### 2. Server Mode
Launch the local FastAPI background microservice:
```bash
git-sniff --server
```
By default, the server runs on `http://127.0.0.1:8000`.

To query the microservice:
```bash
curl "http://127.0.0.1:8000/sniff?repo=langchain-ai/deepagents"
```

---

## Architectural Scoring Pillars

`git-sniff` aggregates scores from 0 to 100 based on four pillars:
1. **Maintenance Vitality (30%)**: Analyzes recent Issue/PR Median Time to Resolution (MTR) and stagnation penalties.
2. **CI/CD & Rigor Compliance (25%)**: Scans file tree recursively for active `.github/workflows/`, code standards configs (e.g. pre-commit, linters), and latest commit check statuses.
3. **Dependency Hygiene (20%)**: Additive partial credit over deterministic repository state — manifest declared (+20), lockfile present (+30), leanness by declared dependency count (+0–30, neutral when unparseable), and automated updates (+20, detected from a Dependabot/Renovate config file in the tree or recent bot commits).
4. **Sustenance Risk / Bus Factor (25%)**: Analyzes velocity distribution among maintainers to highlight single-point-of-failure risks.

See `SPEC.md` for exact boundaries and the rationale behind the dependency-pillar model.

---

## Claude Code plugin & skill

This repo is also a Claude Code plugin (`.claude-plugin/plugin.json`) bundling the **`repo-hygiene`** skill (`skills/repo-hygiene/`). The skill lets Claude score a repo's hygiene on request ("is this repo worth cloning?", "what's the bus factor of …"). It calls the local `/sniff` microservice via `skills/repo-hygiene/scripts/sniff.sh` (which auto-starts the server if needed) and interprets results using `skills/repo-hygiene/references/scoring.md`.

## Authentication

`git-sniff` reads `GITHUB_PERSONAL_ACCESS_TOKEN` from the environment to lift GitHub's 60-requests/hour unauthenticated cap. A zero-scope classic PAT suffices for public-repo reads.
