This is a comprehensive, production-ready spec-driven document designed for an LLM coding engineer (you) to implement git-sniff in one go. Follow it strictly.

# ---

**Specification: git-sniff CLI & Microservice Engine**

## **1\. Project Overview**

git-sniff is a local developer tool written in Python that helps developers instantly evaluate the code quality, architectural health, and maintenance vitality of any public GitHub repository before cloning it.

It operates in two modes:

1. **CLI Mode:** A terminal command (git-sniff owner/repo) that prints a structured, colored ASCII scorecard.  
2. **Server Mode:** A lightweight FastAPI background microservice (git-sniff \--server) that exposes an endpoint for a future Chrome extension to query via JSON.

## ---

**2\. Technical Stack & Constraints**

* **Language:** Python 3.10+  
* **Core Libraries:** \* fastapi, uvicorn (Microservice architecture)  
  * httpx (Asynchronous HTTP requests to GitHub API)  
  * rich (Terminal formatting, tables, and colors)  
  * pydantic (Data validation and settings)  
* **API Targets:** GitHub REST API v3 (and optionally GraphQL v4 if cleaner for contribution stats).  
* **Authentication:** Must support reading a GITHUB\_TOKEN from environment variables to bypass rate limits ($60\\rightarrow5000$ requests/hr). If missing, gracefully fall back to unauthenticated requests with a warning.

## ---

**3\. Directory Layout**

The agent must generate the following file structure:

Plaintext

git-sniff/  
├── pyproject.toml         \# Dependency definitions (FastAPI, Rich, HTTPX)  
├── README.md              \# Usage instructions  
├── git\_sniff/  
│   ├── \_\_init\_\_.py  
│   ├── main.py            \# CLI entrypoint (Typer or argparse)  
│   ├── server.py          \# FastAPI server implementation  
│   ├── client.py          \# GitHub API integration engine  
│   ├── metrics.py         \# Scoring logic and mathematical formulas  
│   └── schemas.py         \# Pydantic data structures  
└── tests/  
    └── test\_metrics.py    \# Unit tests for scoring math

## ---

**4\. Architectural Metrics & Scoring Logic**

The engine must pull raw data from GitHub and calculate an aggregate score from **0 to 100**, weighted across four distinct pillars:

### **Pillar 1: Maintenance Vitality (Weight: 30%)**

* **Endpoint:** /repos/{owner}/{repo}/issues?state=all\&per\_page=100  
* **Logic:** \* Fetch the 100 most recent issues/PRs.  
  * Calculate **Median Time to Resolution (MTR)** for closed items.  
  * Score calculation:  
    * $\\text{MTR} \\le 7 \\text{ days} \\rightarrow 100\\text{ pts}$  
    * $7 \< \\text{MTR} \\le 30 \\text{ days} \\rightarrow 80\\text{ pts}$  
    * $30 \< \\text{MTR} \\le 90 \\text{ days} \\rightarrow 50\\text{ pts}$  
    * $\\text{MTR} \> 90 \\text{ days} \\rightarrow 10\\text{ pts}$  
  * If the ratio of open\_issues to stars exceeds $0.15$ and the project has $\>1000$ stars, apply a $-15$ point "stagnation penalty."

### **Pillar 2: CI/CD & Rigor Compliance (Weight: 25%)**

* **Endpoints:** \* /repos/{owner}/{repo}/contents/ (Root directory tree scan)  
  * /repos/{owner}/{repo}/commits/main/status (or default branch status)  
* **Logic:** Scan the file tree for strict engineering indicators:  
  * Check for .github/workflows/ directory $\\rightarrow \+40\\text{ pts}$  
  * Check for configuration files (.pre-commit-config.yaml, .eslintrc\*, pyproject.toml with linting sections, or .markdownlint.json) $\\rightarrow \+30\\text{ pts}$  
  * Verify if the latest commit check state is success $\\rightarrow \+30\\text{ pts}$ (Partial credit: pending \= $15\\text{ pts}$, failure \= $0\\text{ pts}$).

### **Pillar 3: Dependency Hygiene (Weight: 20%)**

* **Endpoint:** recursive Git tree + /repos/{owner}/{repo}/commits?per\_page=50  
* **Logic:** Evaluate dependency hygiene via additive partial credit over deterministic repository-state signals (capped at $100$):  
  * **Manifest declared** ($+20$): any dependency manifest in the tree (pyproject.toml, package.json, Cargo.toml, go.mod, requirements\*.txt, setup.py/cfg, Pipfile, Gemfile, pom.xml, build.gradle, composer.json, …).  
  * **Lockfile present** ($+30$): any reproducible-install lockfile (package-lock.json, yarn.lock, pnpm-lock.yaml, poetry.lock, uv.lock, Cargo.lock, go.sum, composer.lock, flake.lock, …).  
  * **Leanness** by parsed root dependency count: $\\le 10 \\to +30$, $\\le 25 \\to +20$, $\\le 50 \\to +10$, $\> 50 \\to +0$; count not parseable $\\to$ neutral $+15$.  
  * **Automated updates** ($+20$): a Dependabot/Renovate **config file** in the tree, or dependabot\[bot\]/renovate\[bot\] authoring $\\ge 10\\%$ of the last 50 commits.  
  * Rationale: the prior all-or-nothing bot-commit proxy returned $0$ for $\\sim 80\\%$ of actively-developed repos (corporate and academic alike), measuring commit-window timing rather than hygiene. Config-file detection is stable; partial credit stops zeroing clean repos that lack bots.

### **Pillar 4: The Bus Factor / Sustenance Risk (Weight: 25%)**

* **Endpoint:** /repos/{owner}/{repo}/stats/contributors  
* **Logic:** \* Extract total additions/deletions or commit counts for the top 10 contributors over the last year.  
  * Calculate the percentage of total contributions owned by the \#1 contributor.  
  * Score calculation:  
    * Top contributor has $\> 85\\%$ of total velocity $\\rightarrow 20\\text{ pts}$ (Critical Single Point of Failure)  
    * Top contributor has $60\\% \- 85\\%$ of total velocity $\\rightarrow 60\\text{ pts}$ (Moderate Risk)  
    * Distribution spreads across $\\ge 3$ contributors with individual shares $\< 50\\% \\rightarrow 100\\text{ pts}$ (Highly Sustainable)

## ---

**5\. Interface Specifications**

### **5.1 CLI Output Layout (Terminal Mode)**

When running git-sniff langchain-ai/deepagents, the tool must render a clear layout using Rich:

Plaintext

\================================================================================  
 GIT-SNIFF SCORECARD: langchain-ai/deepagents  
\================================================================================  
 OVERALL SCORE: 88/100 \[🟢 HEALTHY\]  
\----------------------------------------------------------------------  
 📊 METRIC BREAKDOWN:  
  \[🟢\] Maintenance Vitality: 90/100 (Median resolution time: 4 days)  
  \[🟢\] CI/CD & Engineering Rigor: 100/100 (.github workflows found, pre-commit active)  
  \[🟡\] Dependency Hygiene: 75/100 (Dependabot active, but high dependency count)  
  \[🟢\] Bus Factor Sustainability: 85/100 (Distributed among 5 core maintainers)  
\----------------------------------------------------------------------  
 💡 RECOMMENDATION: Production ready. High contribution velocity and modern tooling.  
\================================================================================

### **5.2 API Output Layout (Server Mode)**

When running git-sniff \--server, initialize a Uvicorn app on port 8000\. It must expose an endpoint GET /sniff?repo={owner}/{repo} returning strict JSON validating against this Pydantic schema structure:

Python

class PillarScores(BaseModel):  
    maintenance: int  
    cicd: int  
    dependencies: int  
    bus\_factor: int

class RepoScorecard(BaseModel):  
    repository: str  
    overall\_score: int  
    status: str  \# "HEALTHY", "WARNING", "CRITICAL"  
    breakdown: PillarScores  
    recommendation: str  
    calculated\_at: str

## ---

**6\. Execution Instructions for the Agent**

1. **Initialize Environment:** Set up a clean virtual environment, generate \`pyproject.toml\`, and handle dependencies asynchronously.  
2. **Handle Non-Blocking Failures:** Ensure that if a specific GitHub endpoint returns a \`404\` or \`403\` (e.g., stats are compiling), the metric calculation degrades gracefully instead of crashing the program.  
3. **Write Tests First:** Write unit tests in tests/test\_metrics.py verifying that the boundary mathematics for the scoring metrics accurately return expected outputs under mock JSON telemetry inputs.  
4. **Complete Implementation:** Ensure both the execution entry point in main.py and the local background listening thread in server.py function natively.

## **6.1 Critical Edge-Case Guardrails**

**GitHub API 202 Retry Logic (Bus Factor Fix):** The \`/repos/{owner}/{repo}/stats/contributors\` endpoint often returns an HTTP \`202 Accepted\` status code with an empty body while GitHub compiles stats. The client engine must catch this \`202\`, sleep for 2–3 seconds, and retry up to 3 times. If it still returns a \`202\`, gracefully fall back to parsing a sampling of the last 100 commits from the \`/commits\` endpoint to approximate the top contributor's entropy.

**CORS Configuration for Extension Support:** Because the FastAPI server in \`server.py\` will eventually serve a Chrome extension querying it from a \`github.com\` origin, you must explicitly initialize \`CORSMiddleware\` on the FastAPI application instance. Allow \`https://github.com\` as an origin (or \`\*\` for local testing), along with standard wildcard methods and headers to prevent browser-side CORS blocks.

**Rate-Limit Diagnostics:** The client must extract the \`X-RateLimit-Remaining\` and \`X-RateLimit-Reset\` headers from every GitHub API response. If the remaining call count drops below 10, both the CLI terminal output and the FastAPI JSON payload must surface a visible warning flag (\`\[🟡 WARNING\] Rate limit critically low...\`) advising the user to supply a \`GITHUB\_TOKEN\`.

