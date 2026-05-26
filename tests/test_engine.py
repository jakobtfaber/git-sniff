from git_sniff.schemas import (
    GitSniffError, BadRepoError, RepoNotFoundError, RateLimitedError, EngineError
)


def test_error_hierarchy():
    for cls in (BadRepoError, RepoNotFoundError, RateLimitedError, EngineError):
        assert issubclass(cls, GitSniffError)
    assert issubclass(GitSniffError, Exception)
