#!/usr/bin/env python3
"""Health check for a deployed Vertex AI Agent Engine resource.

Runs the smoke test against an already-deployed resource. Used both as a
standalone CLI — to check a resource without deploying, e.g. before touching a
live agent or right after a rollback — and as the shared helper `deploy.py`
calls immediately after deploying.

Usage:
    uv run python deployment/scripts/health_check.py
    uv run python deployment/scripts/health_check.py --message "list the projects in the registry"
"""

import argparse
import logging
import os
import sys

# Ensure the project root is on the path when run as a script.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def run_smoke_test(
    remote_agent, message: str = "ping", user_id: str = "smoke-test"
) -> bool:
    """Create a server-side session, query it, and require at least one event back.

    Deliberately session-scoped rather than a bare `stream_query`. This mirrors
    the playground flow exactly: `create_session` and `stream_query` are two
    separate calls that may land on different replicas, so a per-replica
    `InMemorySessionService` loses the session between them and the playground
    hangs. A sessionless smoke test passes straight through that bug.
    """
    session = remote_agent.create_session(user_id=user_id)  # type: ignore[attr-defined]
    session_id = session["id"] if isinstance(session, dict) else session.id

    events = list(
        remote_agent.stream_query(  # type: ignore[attr-defined]
            message=message, user_id=user_id, session_id=session_id
        )
    )
    if not events:
        logger.error(
            "Health check failed: no events returned for session %s — the agent did "
            "not respond to a session-scoped query (this is the playground hang).",
            session_id,
        )
        return False

    logger.info("Health check passed: %d event(s) returned.", len(events))
    return True


def check_resource(message: str, user_id: str) -> bool:
    """Fetch the resource named by AGENT_ENGINE_RESOURCE_NAME and smoke-test it."""
    import vertexai
    from vertexai import agent_engines

    from deployment.config import DeploymentConfig

    config = DeploymentConfig.from_env()

    if not config.resource_name:
        logger.error(
            "AGENT_ENGINE_RESOURCE_NAME is not set — health check needs an existing "
            "deployed resource to query, not a fresh deploy."
        )
        return False

    logger.info("Health-checking: %s", config.resource_name)

    vertexai.init(project=config.project, location=config.location)
    remote_agent = agent_engines.get(config.resource_name)

    return run_smoke_test(remote_agent, message=message, user_id=user_id)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Run a health check against a deployed Agent Engine resource"
    )
    parser.add_argument(
        "--message",
        default="ping",
        help=(
            "Message to send for the smoke test (default: ping). Pass a real "
            "tool-exercising query to verify more than liveness."
        ),
    )
    parser.add_argument(
        "--user-id",
        default="health-check",
        help="User ID to attribute the smoke-test query to (default: health-check)",
    )
    args = parser.parse_args()
    sys.exit(0 if check_resource(args.message, args.user_id) else 1)
