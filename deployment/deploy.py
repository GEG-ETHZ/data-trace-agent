#!/usr/bin/env python3
"""Deploy the agent to Vertex AI Agent Engine.

Usage:
    uv run python deployment/deploy.py --env dev
    uv run python deployment/deploy.py --env prod
"""

import argparse
import logging
import os
import sys
from pathlib import Path

# Ensure the project root is on the path when run as a script
# (python deployment/deploy.py puts deployment/ on sys.path, not the root).
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def deploy(env: str) -> None:
    import vertexai
    from vertexai import agent_engines

    from agent.agent import root_agent
    from deployment.config import DeploymentConfig, runtime_env_vars

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

    requirements = [
        "google-adk>=1.0.0",
        "google-cloud-aiplatform[agent_engines]>=1.90.0",
        "litellm>=1.50.0",
        "pydantic>=2.0.0",
        "python-dotenv>=1.0.0",
        "pyyaml>=6.0",
        "gitpython>=3.1.44",
        "pandas>=2.0.0",
        "pyarrow>=14.0.0",
        "dvc[all]>=3.0.0",
    ]

    # Local source that must be importable on the remote container. The pickled
    # agent references agent.tools.* by module path, so the package has to ship
    # alongside it (the prompts dir travels too for any runtime reads).
    extra_packages = ["agent", "prompts"]

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
    logger.info("Deployed: %s", resource_name)

    Path(".agent_engine_resource").write_text(resource_name + "\n")

    logger.info("Running smoke test...")
    # Mirror the playground flow exactly: create a server-side session first,
    # then stream_query against that session_id. These are two separate calls
    # that may hit different replicas, so this catches session-service
    # misconfigurations (e.g. a per-replica InMemorySessionService) that a
    # sessionless stream_query would silently pass.
    session = remote_agent.create_session(user_id="smoke-test")  # type: ignore[attr-defined]
    session_id = session["id"] if isinstance(session, dict) else session.id
    events = list(
        remote_agent.stream_query(  # type: ignore[attr-defined]
            message="ping", user_id="smoke-test", session_id=session_id
        )
    )
    if not events:
        logger.error(
            "Smoke test returned no events for session %s — the agent did not "
            "respond to a session-scoped query (this is the playground hang).",
            session_id,
        )
        sys.exit(1)
    logger.info("Smoke test passed.")

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
