import subprocess
import git_sniff.auth as auth_mod
from git_sniff.auth import resolve_token


class _CompletedProcess:
    def __init__(self, returncode, stdout=""):
        self.returncode = returncode
        self.stdout = stdout


def test_keychain_hit(monkeypatch):
    monkeypatch.delenv("GITHUB_PERSONAL_ACCESS_TOKEN", raising=False)
    monkeypatch.setattr(
        auth_mod.subprocess, "run",
        lambda *a, **k: _CompletedProcess(0, "ghp_keychaintoken\n"),
    )
    assert resolve_token() == "ghp_keychaintoken"


def test_env_fallback_when_keychain_misses(monkeypatch):
    monkeypatch.setattr(
        auth_mod.subprocess, "run",
        lambda *a, **k: _CompletedProcess(44, ""),
    )
    monkeypatch.setenv("GITHUB_PERSONAL_ACCESS_TOKEN", "ghp_envtoken")
    assert resolve_token() == "ghp_envtoken"


def test_none_when_neither(monkeypatch):
    monkeypatch.setattr(
        auth_mod.subprocess, "run",
        lambda *a, **k: _CompletedProcess(44, ""),
    )
    monkeypatch.delenv("GITHUB_PERSONAL_ACCESS_TOKEN", raising=False)
    assert resolve_token() is None


def test_keychain_exception_falls_through(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError("security not found")
    monkeypatch.setattr(auth_mod.subprocess, "run", boom)
    monkeypatch.setenv("GITHUB_PERSONAL_ACCESS_TOKEN", "ghp_envtoken")
    assert resolve_token() == "ghp_envtoken"
