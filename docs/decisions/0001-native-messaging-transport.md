# 0001 — Native Messaging as the extension's sole transport

**Status:** Accepted
**Date:** 2026-05-26
**Design:** `docs/superpowers/specs/2026-05-26-native-messaging-host-design.md`

## Context

The Chrome extension queries repository health by `fetch`-ing a localhost FastAPI
server (`http://127.0.0.1:8000/sniff`). That model requires a long-lived,
shell-launched server, exposes a localhost HTTP endpoint with broad CORS, and reads the
GitHub token from the shell environment. Keeping the server alive for a browser
extension would otherwise pull in a launchd agent or another always-on daemon — plumbing
specific to git-sniff rather than the idiomatic browser pattern.

Chrome Native Messaging lets the extension invoke a local process **on demand**: Chrome
reads a host manifest, spawns a short-lived stdio process per query, and tears it down
when stdin closes. No daemon, no open port, no CORS surface.

## Decision

1. **Native messaging is the sole transport for the extension.** The extension keeps
   **no** HTTP fallback path. Removing the localhost dependency is the whole point;
   leaving an HTTP fallback in the extension would preserve the daemon/port concern.

2. **The FastAPI `--server` mode is retained but deprecated** as a legacy/manual adapter
   for the CLI, `skills/repo-hygiene/scripts/sniff.sh`, and ad-hoc curl. It is **not**
   removed in this change, to avoid expanding an extension-transport refactor into a
   tool-surface redesign.

3. **No launchd agent and no always-on daemon** are introduced.

4. **Shared, auth-agnostic core.** Orchestration moves to `engine.evaluate(owner, repo,
   *, token=None, http_client=None)`. Adapters resolve auth and own the client
   lifecycle; the engine does neither.

5. **Token resolution is Keychain → env → unauthenticated**, via `auth.resolve_token()`
   using an explicit `/usr/bin/security` argv (no `shell=True`). Keychain-first is what
   makes the Chrome-spawned host work (it has no shell env); the env fallback keeps
   CLI/`--server` behavior unchanged.

6. **Extension ID is pinned** via a `key` in `manifest.json`; `native_host.py` holds the
   derived `EXTENSION_ID` and `HOST_NAME` as the single source of truth the installer
   writes into `allowed_origins`.

## Removal criteria for the HTTP server

Delete `git-sniff --server`, `git_sniff/server.py`, and the `fastapi`/`uvicorn`
dependencies once **both** hold:

1. `sniff.sh` is migrated to a `git-sniff --json owner/repo` CLI mode over `evaluate()`,
   and
2. there are no known curl-based `/sniff` consumers.

This is tracked as a follow-up; the server stays deprecated-but-functional until then.

## Alternatives considered

- **Coexist with HTTP as an extension fallback** — rejected: preserves the localhost
  dependency the refactor exists to remove.
- **Full cutover (delete FastAPI now)** — rejected for this pass: breaks the
  repo-hygiene/curl workflow before the native host is proven; widens scope.
- **launchd-managed always-on server** — rejected: git-sniff-specific plumbing; native
  messaging is the idiomatic on-demand extension→local-process pattern.
- **Persistent `connectNative` port** — rejected: one-shot `sendNativeMessage` matches
  the request/response scorecard with no lifecycle to manage.

## Consequences

- **Positive:** no open localhost port or CORS surface for normal extension use; token
  never traverses HTTP; no daemon/launchd; host spawned only when querying a repo.
- **Negative / cost:** per-query Python cold start (import cost) instead of a warm
  server; host registration is an explicit install step (`git-sniff-host --install`);
  end-to-end verification is manual (no live Chrome in CI); macOS/Chrome-only this pass.
- **Migration:** `content.js` is untouched; `background.js` swaps `fetch` for
  `sendNativeMessage` behind the same message contract; `manifest.json` drops localhost
  `host_permissions`, adds `nativeMessaging` + pinned `key`.
