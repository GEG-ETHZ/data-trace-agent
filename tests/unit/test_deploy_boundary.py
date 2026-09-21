"""Guards for the deploy-time boundary between `agent/` and `deployment/`.

`agent/agent.py` and `agent/agents/*` import `deployment.config.resolve_model`, but
`deployment/` is not in `extra_packages` and never reaches the Agent Engine container.
That is safe only because cloudpickle stores tool functions *by reference*, so the
container's import closure is `agent/__init__` + `agent/tools/*` and never touches the
modules that import `deployment`.

The invariant is invisible in the source and breaks silently: a stray
`from deployment.x import y` inside `agent/tools/` or `agent/__init__.py` still deploys
green and then fails on the first real request. These tests fail at CI time instead.
"""

import pytest

from deployment.deploy import (
    UNSHIPPED_PACKAGES,
    preflight_import_closure,
    requirements_from_pyproject,
)


def test_requirements_come_from_pyproject():
    """The deploy list is derived, not a hand-maintained copy that can drift."""
    requirements = requirements_from_pyproject()

    assert requirements, "no dependencies read from pyproject.toml"
    names = " ".join(requirements)
    # Both were missing from the previous hardcoded list and survived only as
    # transitive deps of google-cloud-aiplatform. bigquery_tools imports
    # google.cloud.bigquery at module scope, so it is on every tool's import path.
    assert "google-cloud-bigquery" in names
    assert "db-dtypes" in names


def test_deployment_is_not_shipped():
    """`deployment` must stay out of extra_packages, or the guard is pointless."""
    assert "deployment" in UNSHIPPED_PACKAGES


def test_root_agent_unpickles_without_deployment():
    """The real agent's import closure must not reach `deployment`.

    This is the regression guard: it pickles the actual `root_agent` and reloads it
    in a subprocess where importing `deployment` raises, exactly as the container
    would behave if something reached for it.
    """
    from agent.agent import root_agent

    # Exits non-zero (SystemExit) if the closure touches an unshipped package.
    preflight_import_closure(root_agent)


def test_preflight_rejects_an_object_that_needs_deployment():
    """The blocker actually blocks — a negative case, so the test above means something.

    `resolve_model` lives in `deployment.config`, and cloudpickle stores a
    module-level function by reference, so unpickling it must import `deployment`.
    """
    from deployment.config import resolve_model

    with pytest.raises(SystemExit):
        preflight_import_closure(resolve_model)
