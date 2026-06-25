"""Shared pytest fixtures for the unit suite.

``deployment.config`` calls ``load_dotenv()`` at import time, so a developer's
local ``.env`` (with real ``REPO_URL`` / ``GIT_AUTH_*`` / ``DVC_CONFIG_LOCAL*``
values) would otherwise leak into tests and make results depend on the machine.
CI has no ``.env``, so this only bites locally — the fixture below keeps both
environments deterministic by clearing those variables before every test. A
test that needs one set still does so explicitly via ``monkeypatch.setenv``.
"""

import pytest

_LEAKY_ENV = (
    "REPO_URL",
    "GIT_AUTH_TOKEN",
    "GIT_AUTH_TOKEN_SECRET",
    "GIT_AUTH_TOKEN_SECRET_VERSION",
    "GIT_AUTH_HOST",
    "GIT_AUTH_USERNAME",
    "DVC_CONFIG_LOCAL",
    "DVC_CONFIG_LOCAL_SECRET",
    "DVC_CONFIG_LOCAL_SECRET_VERSION",
)


@pytest.fixture(autouse=True)
def _isolate_repo_env(monkeypatch):
    """Clear repo/Git/DVC environment variables before each test."""
    for name in _LEAKY_ENV:
        monkeypatch.delenv(name, raising=False)
