# git-sniff

An instant architectural, maintenance, and compliance health scorecard for public GitHub repositories. Query repo stats in your terminal before cloning, emit them as JSON for scripting, or surface them on GitHub via a Chrome extension backed by an on-demand native-messaging host.

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

### 2. JSON Mode (scripting)
Emit the scorecard as JSON on stdout — for piping into `jq`, CI, or the
`repo-hygiene` skill. Prints `{"error": "..."}` and exits 1 on failure:
```bash
git-sniff --json langchain-ai/deepagents
```

### 3. Chrome extension (Native Messaging)

The extension talks to a local host that Chrome spawns on demand — no server, no open
port. Register the host once:

```bash
pip install -e .            # provides the git-sniff-host binary
git-sniff-host --install    # writes the Chrome native-host manifest
git-sniff-host --status     # verify path + allowed origin
```

Then load `extension/` unpacked at `chrome://extensions`. The host reads its GitHub token
from the macOS Keychain (service `Agents`, account `github-pat`), falling back to the
`GITHUB_PERSONAL_ACCESS_TOKEN` environment variable.

Re-run `git-sniff-host --install` after any Python/venv reinstall (the manifest records an
absolute path that a reinstall can invalidate). `git-sniff-host --uninstall` removes it.

---

## Architectural Scoring Pillars

`git-sniff` aggregates scores from 0 to 100 based on four pillars:
1. **Maintenance Vitality (30%)**: Analyzes recent Issue/PR Median Time to Resolution (MTR) and stagnation penalties.
2. **CI/CD & Rigor Compliance (25%)**: Scans file tree recursively for active `.github/workflows/`, code standards configs (e.g. pre-commit, linters), and latest commit check statuses.
3. **Dependency Hygiene (15%)**: Additive partial credit over deterministic repository state — manifest declared (+20), lockfile present (+30), leanness by declared dependency count (+0–30, neutral when unparseable), and automated updates (+20, detected from a Dependabot/Renovate config file in the tree or recent bot commits).
4. **Sustenance Risk / Bus Factor (30%)**: Analyzes velocity distribution among maintainers to highlight single-point-of-failure risks.

See `SPEC.md` for exact boundaries and the rationale behind the dependency-pillar model.

---

## Claude Code plugin & skill

This repo is also a Claude Code plugin (`.claude-plugin/plugin.json`) bundling the **`repo-hygiene`** skill (`skills/repo-hygiene/`). The skill lets Claude score a repo's hygiene on request ("is this repo worth cloning?", "what's the bus factor of …"). It calls `git-sniff --json` via `skills/repo-hygiene/scripts/sniff.sh` and interprets results using `skills/repo-hygiene/references/scoring.md`.

## Authentication

`git-sniff` reads `GITHUB_PERSONAL_ACCESS_TOKEN` from the environment to lift GitHub's 60-requests/hour unauthenticated cap. A zero-scope classic PAT suffices for public-repo reads.
