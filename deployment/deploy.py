#!/usr/bin/env python3
"""Deploy the agent to Vertex AI Agent Engine.

Usage:
    uv run python deployment/deploy.py --env dev
    uv run python deployment/deploy.py --env prod
"""

import argparse
import logging
import os
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

# Ensure the project root is on the path when run as a script
# (python deployment/deploy.py puts deployment/ on sys.path, not the root).
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent


def requirements_from_pyproject() -> list[str]:
    """Read the deploy requirements from `[project].dependencies`.

    Derived rather than restated: a hand-maintained copy of this list silently
    drifts, and the failure lands at request time in the container rather than at
    deploy time. `google-cloud-bigquery` and `db-dtypes` were already missing from
    the previous hardcoded list and survived only as transitive dependencies.
    """
    with open(PROJECT_ROOT / "pyproject.toml", "rb") as f:
        return list(tomllib.load(f)["project"]["dependencies"])


# Local top-level packages that exist in the repo but are deliberately NOT shipped
# to the container. `deployment` is the one that matters: `agent/agent.py` and
# `agent/agents/*` import `deployment.config.resolve_model`, which is fine only
# because cloudpickle stores tool functions by reference, so the container's import
# closure is `agent/__init__` + `agent/tools/*` and never reaches those modules.
# That invariant is one stray import away from breaking, and it breaks silently at
# request time. The preflight below turns it into a deploy-time failure instead.
UNSHIPPED_PACKAGES = ("deployment", "tests")

_PREFLIGHT_SCRIPT = '''
import sys, pathlib

BLOCKED = set(sys.argv[2].split(","))


class _Blocker:
    """Refuse imports of packages that will not exist in the container."""

    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in BLOCKED:
            raise ModuleNotFoundError(
                f"{fullname!r} is not shipped to the Agent Engine container. "
                "The pickled agent must not depend on it at import time."
            )
        return None


sys.meta_path.insert(0, _Blocker())

import cloudpickle

agent = cloudpickle.loads(pathlib.Path(sys.argv[1]).read_bytes())

model = getattr(agent, "model", None)
if not isinstance(model, str) and type(model).__name__ != "LiteLlm":
    raise SystemExit(
        f"root_agent.model resolved to {type(model).__name__}, expected str or LiteLlm. "
        "resolve_model() must produce a concrete value at pickle time."
    )
print(f"OK model={model if isinstance(model, str) else type(model).__name__}")
'''


def preflight_import_closure(root_agent: object) -> None:
    """Prove the pickled agent loads without the packages that never ship.

    Pickles `root_agent` here, then unpickles it in a clean subprocess where every
    module in `UNSHIPPED_PACKAGES` raises `ModuleNotFoundError` on import. This
    reproduces the container's import closure on the deploy machine, so a stray
    `deployment.*` import in `agent/` fails loudly now rather than silently at the
    first request after a green deploy.
    """
    import cloudpickle

    logger.info("Preflight: checking the pickled agent's import closure...")
    with tempfile.TemporaryDirectory() as tmp:
        payload = Path(tmp) / "agent.pkl"
        payload.write_bytes(cloudpickle.dumps(root_agent))
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                _PREFLIGHT_SCRIPT,
                str(payload),
                ",".join(UNSHIPPED_PACKAGES),
            ],
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
        )
    if result.returncode != 0:
        logger.error(
            "Preflight failed — the pickled agent depends on a package that is not "
            "shipped to the container (extra_packages). Deploy aborted.\n%s",
            (result.stderr or result.stdout).strip(),
        )
        sys.exit(1)
    logger.info("Preflight passed: %s", result.stdout.strip())


def deploy(env: str) -> None:
    import vertexai
    from vertexai import agent_engines

    from agent.agent import root_agent
    from deployment.config import DeploymentConfig, runtime_env_vars
    from deployment.scripts.health_check import run_smoke_test

    config = DeploymentConfig.from_env()
    env_vars = runtime_env_vars()

    logger.info("Deploying [%s] to Vertex AI Agent Engine", env)
    logger.info("  Project:  %s", config.project)
    logger.info("  Location: %s", config.location)
    logger.info("  Bucket:   %s", config.staging_bucket)
    if config.service_account:
        logger.info("  Service account: %s", config.service_account)
    # Log which env vars are forwarded — never their values (may hold a token).
    if env_vars:
        logger.info("  Env vars: %s", ", ".join(sorted(env_vars)))

    vertexai.init(
        project=config.project,
        location=config.location,
        staging_bucket=config.staging_bucket,
    )

    requirements = requirements_from_pyproject()
    logger.info("  Requirements: %d from pyproject.toml", len(requirements))

    # Local source that must be importable on the remote container. The pickled
    # agent references agent.tools.* by module path, so the package has to ship
    # alongside it (the prompts dir travels too for any runtime reads).
    extra_packages = ["agent", "prompts"]

    preflight_import_closure(root_agent)

    # Pass root_agent directly so Agent Engine wraps it in AdkApp and lets
    # set_up() auto-select VertexAiSessionService (server-managed, shared across
    # replicas) when GOOGLE_CLOUD_AGENT_ENGINE_ID is present in the container.
    # The playground does create_session then stream_query(session_id=...) as two
    # separate calls that may hit different replicas — an InMemorySessionService
    # would lose the session between them and the playground would hang. The
    # service-account ADC in the container authenticates the SessionService API
    # (it is not blocked; sibling agents in this project use it).
    if config.resource_name:
        logger.info("  Updating: %s", config.resource_name)
        existing = agent_engines.get(config.resource_name)
        remote_agent = existing.update(
            agent_engine=root_agent,
            requirements=requirements,
            extra_packages=extra_packages,
            gcs_dir_name=config.gcs_dir_name,
            env_vars=env_vars or None,
            service_account=config.service_account,
        )
    else:
        logger.info("  Creating new Agent Engine resource...")
        remote_agent = agent_engines.create(
            agent_engine=root_agent,
            requirements=requirements,
            display_name=config.agent_display_name,
            extra_packages=extra_packages,
            gcs_dir_name=config.gcs_dir_name,
            env_vars=env_vars or None,
            service_account=config.service_account,
        )

    resource_name = remote_agent.resource_name

    # An in-place update must land on the resource we targeted. If it silently
    # created a new one instead, the live agent is untouched and consumers still
    # point at the old resource — a "successful" deploy that changed nothing.
    if config.resource_name and resource_name != config.resource_name:
        logger.error(
            "Expected to update %s but the deploy returned %s. The live resource "
            "was not updated.",
            config.resource_name,
            resource_name,
        )
        sys.exit(1)

    logger.info("Deployed: %s", resource_name)

    Path(".agent_engine_resource").write_text(resource_name + "\n")

    logger.info("Running smoke test...")
    # Shared with `make health-check`, so a post-deploy check and a standalone
    # check can never drift into testing different things.
    if not run_smoke_test(remote_agent):
        sys.exit(1)

    # Emit for CI capture
    logger.info("AGENT_ENGINE_RESOURCE_NAME=%s", resource_name)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Deploy agent to Vertex AI Agent Engine"
    )
    parser.add_argument(
        "--env",
        choices=["dev", "prod"],
        default="prod",
        help="Target environment (default: prod)",
    )
    args = parser.parse_args()
    deploy(args.env)
