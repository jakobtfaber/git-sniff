import os
import subprocess
from typing import Optional

KEYCHAIN_SERVICE = "Agents"
KEYCHAIN_ACCOUNT = "github-pat"
ENV_VAR = "GITHUB_PERSONAL_ACCESS_TOKEN"


def _keychain_token() -> Optional[str]:
    try:
        result = subprocess.run(
            [
                "/usr/bin/security", "find-generic-password",
                "-s", KEYCHAIN_SERVICE,
                "-a", KEYCHAIN_ACCOUNT,
                "-w",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return None
    if result.returncode == 0:
        token = result.stdout.strip()
        return token or None
    return None


def resolve_token() -> Optional[str]:
    """Resolve a GitHub token: macOS Keychain first, then env var, else None."""
    token = _keychain_token()
    if token:
        return token
    return os.getenv(ENV_VAR) or None
