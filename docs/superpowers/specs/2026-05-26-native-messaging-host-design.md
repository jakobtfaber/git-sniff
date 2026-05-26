# Design: git-sniff Native Messaging Host

**Date:** 2026-05-26
**Status:** Approved (brainstorming) — pending implementation plan
**Related ADR:** `docs/decisions/0001-native-messaging-transport.md`

## Problem

The Chrome extension is hard-wired to `fetch("http://127.0.0.1:${apiPort}/sniff?repo=…")`
(`extension/background.js:8-19`). This requires the user to keep a FastAPI server
(`git-sniff --server --port 8000`) running, exposes a localhost HTTP endpoint with
broad CORS (`allow_origins=[…, "*"]`, `git_sniff/server.py:51`), and reads the GitHub
token from the shell environment — a model that only works while a long-lived,
shell-launched daemon is up.

The goal is to make the extension talk to a local process **on demand** via Chrome
**Native Messaging**: Chrome spawns a short-lived stdio host per query, no always-on
daemon, no launchd agent, no localhost dependency for normal extension use.

## Decision summary (see ADR 0001)

- Native messaging becomes the **sole** extension transport. The extension keeps **no**
  HTTP fallback path.
- The FastAPI `--server` mode is **retained but deprecated** as a legacy/manual adapter
  (CLI scripting, `skills/repo-hygiene/scripts/sniff.sh`, ad-hoc curl). It is **not**
  removed in this pass. Removal criteria are stated below and in the ADR.
- No launchd agent, no always-on daemon.

## Architecture — shared core, three adapters

Today `git_sniff/server.py:sniff_repository` (lines 74-143) contains the full
orchestration: metadata → concurrent fetch (issues/tree/status/commits/contributors)
→ 202 contributor fallback → dependency count → four pillar scores → overall score →
`RepoScorecard`. That orchestration is transport-coupled (it lives inside a FastAPI
route and raises `HTTPException`).

Extract it into a transport-agnostic, **auth-agnostic** core:

```
git_sniff/
├── engine.py        # NEW: async evaluate(); pure orchestration, typed errors
├── auth.py          # NEW: resolve_token(); Keychain → env → None
├── native_host.py   # NEW: stdio framing + Chrome manifest install/uninstall/status
├── server.py        # adapter (FastAPI) — DEPRECATED, retained; calls evaluate()
├── main.py          # adapter (CLI/Rich); calls evaluate()
├── client.py        # unchanged (GitHub API integration)
├── metrics.py       # unchanged (scoring math)
└── schemas.py       # + typed error classes
```

### `engine.evaluate` — the single orchestration path

```python
async def evaluate(
    owner: str,
    repo: str,
    *,
    token: str | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> RepoScorecard:
    ...
```

- **Transport-agnostic:** returns a `RepoScorecard`, never an HTTP response. Raises
  typed errors (below). Each adapter maps errors to its own surface.
- **Auth-agnostic:** the caller resolves the token and passes it in. The engine does
  **not** call Keychain/env. This lets FastAPI reuse its lifespan-managed shared
  `httpx.AsyncClient` while the CLI/native host create their own client lifecycle, and
  lets tests inject a mock client.
- **Client lifecycle:** if `http_client` is `None`, `evaluate` creates and closes its
  own `httpx.AsyncClient` (CLI/native-host case). If provided, it uses it and does
  **not** close it (FastAPI lifespan owns it).
- The 202 `/stats/contributors` → commit-authorship fallback (`server.py:93-106`) moves
  into the engine unchanged.

### Typed errors (`schemas.py`)

```
GitSniffError(Exception)              # base
├── BadRepoError        # malformed owner/repo
├── RepoNotFoundError   # 404 / private
├── RateLimitedError    # GitHub 403 rate limit
└── EngineError         # unexpected internal failure
```

Adapter mapping:

| Error | FastAPI (legacy) | Native host | CLI |
|---|---|---|---|
| `BadRepoError` | 400 `{detail}` | `{"error": …}` | Rich error, exit 2 |
| `RepoNotFoundError` | 404 `{detail}` | `{"error": …}` | Rich error, exit 1 |
| `RateLimitedError` | 403 `{detail}` | `{"error": …}` | Rich warning |
| `EngineError` | 500 `{detail}` | `{"error": …}` | Rich error, exit 1 |

FastAPI's existing HTTP status mapping (`server.py:145-162`) is preserved exactly —
the route becomes a thin `try evaluate()/except GitSniffError → HTTPException` shim.

## Authentication — `auth.py: resolve_token()`

Resolution order (first hit wins):

1. **macOS Keychain** via `subprocess.run` with an explicit argv (no `shell=True`):
   `/usr/bin/security find-generic-password -s Agents -a github-pat -w`
   (verified item: `svce="Agents"`, `acct="github-pat"`).
2. **Env var** `GITHUB_PERSONAL_ACCESS_TOKEN`.
3. **`None`** → unauthenticated; adapters surface the existing rate-limit warning.

All three adapters call `resolve_token()` and pass the result into `evaluate(token=…)`.
CLI/`--server` behavior is unchanged because the env var still resolves (Keychain is
tried first but simply adds a path that works for the Chrome-spawned host, which has no
shell env).

## Native messaging protocol

Transport: `chrome.runtime.sendNativeMessage(hostName, message, callback)` — one-shot.
Chrome spawns the host, delivers one message, waits for one reply, then closes stdin and
the host exits. This matches the request/response scorecard with no persistent port.

### Wire framing

- **32-bit native-endian** unsigned length prefix, followed by that many bytes of UTF-8
  JSON. (On macOS this is little-endian in practice, but the Chrome spec mandates native
  byte order; implement with explicit `struct.pack("@I", n)` / `struct.unpack("@I", …)`
  helpers and test the round trip.)
- Chrome → host message max **1 MB**; host → Chrome response max **1 MB**. A
  `RepoScorecard` is well under this bound.

### Messages

- **Request (Chrome → host):** `{"owner": "<owner>", "repo": "<repo>"}`
- **Success reply (host → Chrome):** the `RepoScorecard` JSON — **identical shape** to
  today's `/sniff` body, so `content.js` rendering needs no change.
- **Error reply (host → Chrome):** `{"error": "<message>"}` — preserves the
  `{detail}`→error-string contract `background.js:24-33` already expects.

### Host stdout discipline

In host mode the process writes **only** framed JSON messages to stdout. All logging
goes to **stderr**. A stray `print()` or library banner on stdout corrupts the stream
and breaks the extension — enforced by test (see Testing).

### Host-side timeout

`sendNativeMessage` is not an abortable fetch, so the host enforces its own deadline:

```python
scorecard = await asyncio.wait_for(evaluate(owner, repo, token=tok), timeout=30)
```

On `asyncio.TimeoutError` the host replies
`{"error": "Connection timed out. GitHub statistics took too long to compile."}`
(preserving today's 30 s user-facing semantics from `background.js:14`). `background.js`
may additionally apply a JS-side Promise timeout for UX, but correctness depends on the
host's own timeout.

## `git-sniff-host` console entry point

A dedicated `[project.scripts]` entry `git-sniff-host` (pip bakes the venv interpreter
into its shebang → stable absolute path, no PATH/conda-activation dependency). The
**executable itself** decides its mode — the Chrome manifest has no `args` field and
Chrome appends its own argv (origin; plus a window handle on Windows):

| Invocation | Mode |
|---|---|
| `git-sniff-host` (or with Chrome's appended argv) | **stdio host mode** — read one framed request, reply, exit; stdout = framed JSON only |
| `git-sniff-host --install` | write/update the Chrome native-host manifest (idempotent, atomic) |
| `git-sniff-host --uninstall` | remove the manifest |
| `git-sniff-host --status` | print: manifest path, registered host path, allowed origin, whether host path exists/executable, whether pinned extension ID matches expected |

Mode detection: explicit `--install`/`--uninstall`/`--status` flags select management
modes; **anything else (including Chrome's origin argv) is host mode.** Host mode is the
default so Chrome's spawn — which passes the origin as argv — always lands in stdio mode.

## Registration / manifest generation

Target **Google Chrome on macOS only** this pass:

```
~/Library/Application Support/Google/Chrome/NativeMessagingHosts/com.jakobtfaber.git_sniff.json
```

Manifest body (exact):

```json
{
  "name": "com.jakobtfaber.git_sniff",
  "description": "git-sniff native messaging host",
  "path": "<absolute path to git-sniff-host>",
  "type": "stdio",
  "allowed_origins": ["chrome-extension://<pinned-id>/"]
}
```

- No `args`. `path` is the absolute resolved location of the `git-sniff-host` console
  script (`shutil.which("git-sniff-host")` → `os.path.realpath`).
- Write **atomically** (temp file in the target dir + `os.replace`) and **idempotently**
  (re-running `--install` overwrites cleanly).
- Structure the installer so Chrome-for-Testing / Chromium target dirs can be added
  later (a table of `{browser: manifest_dir}`) without changing host architecture.

## Extension ID / manifest key — single source of truth

The host manifest's `allowed_origins` needs a **stable** `chrome-extension://<id>/`.
Unpacked extensions otherwise get a path-derived ID that drifts across loads/machines.

- Pin the ID with a `"key"` field in `extension/manifest.json` (base64 public key). The
  ID is the deterministic hash Chrome derives from that key.
- **Key production (documented in the spec/README):** generate an RSA keypair; the
  manifest `key` is the base64-encoded DER public key; the extension ID is computed from
  it (Chrome's hashed-key → 32-char `a-p` mapping). Record the resulting ID once.
- **Shared constant:** the pinned ID lives in **one** place the installer reads —
  `git_sniff/native_host.py` holds `EXTENSION_ID` and `HOST_NAME =
  "com.jakobtfaber.git_sniff"`, and the installer writes `allowed_origins` from it.
  `--status` compares the installed manifest's origin against `EXTENSION_ID` and reports
  drift. The private key is **not** committed; only the public `key` (in the extension
  manifest) and the derived ID (in source) are.

## Extension changes

**`manifest.json`:**
- Remove `host_permissions` localhost entries (`manifest.json:9-12`).
- Add `"nativeMessaging"` to `permissions`.
- Add the pinned `"key"`.

**`background.js`:** replace the `fetch(...)` block (lines 5-37) with:

```js
chrome.runtime.sendNativeMessage("com.jakobtfaber.git_sniff", { owner, repo }, (resp) => {
  if (chrome.runtime.lastError) {
    sendResponse({ success: false,
      error: "Native host not installed. Run: git-sniff-host --install" });
    return;
  }
  if (!resp) {
    sendResponse({ success: false, error: "No response from git-sniff native host." });
    return;
  }
  if (resp.error) { sendResponse({ success: false, error: resp.error }); return; }
  sendResponse({ success: true, data: resp });
});
return true;
```

Error mapping preserved:
- `chrome.runtime.lastError` (host not found / manifest wrong / forbidden origin) →
  "Native host not installed. Run: git-sniff-host --install"
- empty/`undefined` response → "No response from git-sniff native host."
- `{error}` from host → passed through as the user-facing string
- (optional) JS-side Promise timeout for UX; host enforces the authoritative 30 s.

**`content.js`:** **untouched.** It still sends `{action:"fetchScorecard", owner, repo}`
and consumes `{success, data}`/`{success, error}`. SPA detection (700 ms location poll +
`popstate` + `turbo:load` + `pjax:end`), reserved-path ignore-list, and `.git` strip are
all unchanged.

## Contract preservation (what must not change)

- `content.js` → `background.js` message: `{action:"fetchScorecard", owner, repo}`.
- `background.js` → `content.js` reply: `{success:true, data}` / `{success:false, error}`.
- Scorecard JSON shape (`RepoScorecard`): identical across `/sniff` and the native host.
- FastAPI `/sniff` HTTP status codes and `{detail}` bodies (legacy path).

## Testing

| Test | Asserts |
|---|---|
| **Engine parity** | server route, CLI, and native host all reach the same `evaluate()` path (one orchestration, no divergence). |
| **Token resolver** | Keychain hit (subprocess mocked); Keychain miss → env fallback; neither → `None`. |
| **Native framing** | `encode(decode(x)) == x` for length-prefix framing; correct `@I` byte order; multi-byte UTF-8. |
| **Host stdout discipline** | in host mode, stdout contains only framed JSON — no banner/log/print leakage. |
| **Installer** | writes the exact manifest JSON, atomically, with absolute `path` and pinned `allowed_origins`; idempotent re-install. |
| **`--status`** | reports drift when installed origin ≠ `EXTENSION_ID`. |
| **`test_metrics.py`** | unchanged — the existing 15 still pass. |
| **background mapping** | small JS unit coverage of `{error}`/`lastError`/empty→error-string mapping (optional); **no live Chrome in CI** — `chrome://` is unreachable to automation, so extension reload + end-to-end is a documented manual verification step. |

## Removal criteria for the HTTP server (tracked in ADR 0001)

Delete `git-sniff --server`, `git_sniff/server.py`, and the `fastapi`/`uvicorn`
dependencies once **both** hold:
1. `skills/repo-hygiene/scripts/sniff.sh` is migrated to `git-sniff --json owner/repo`
   (a CLI JSON mode over `evaluate()`), and
2. there are no known curl-based `/sniff` consumers.

Until then the server remains as a deprecated manual adapter.

## Out of scope (YAGNI)

- launchd agent / always-on daemon.
- Chrome-for-Testing / Chromium / Firefox / Edge host registration (installer is
  *structured* for it; not implemented).
- Persistent `connectNative` port (one-shot suffices).
- Removing FastAPI in this pass.
