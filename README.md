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
   export GITHUB_TOKEN="your_personal_access_token"
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
3. **Dependency Hygiene (20%)**: Checks active automated dependency managers (Dependabot/Renovate bot commits) and applies dependency count bloat penalties (>40).
4. **Sustenance Risk / Bus Factor (25%)**: Analyzes velocity distribution among maintainers to highlight single-point-of-failure risks.
