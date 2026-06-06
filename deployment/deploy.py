#!/usr/bin/env python3
"""Deploy the agent to Vertex AI Agent Engine.

Usage:
    uv run python deployment/deploy.py --env dev
    uv run python deployment/deploy.py --env prod
"""

import argparse
import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def deploy(env: str) -> None:
    import vertexai
    from vertexai import agent_engines

    from agent.agent import root_agent
    from deployment.config import DeploymentConfig

    config = DeploymentConfig.from_env()

    logger.info("Deploying [%s] to Vertex AI Agent Engine", env)
    logger.info("  Project:  %s", config.project)
    logger.info("  Location: %s", config.location)
    logger.info("  Bucket:   %s", config.staging_bucket)

    vertexai.init(project=config.project, location=config.location)

    requirements = [
        "google-adk>=1.0.0",
        "google-cloud-aiplatform[agent_engines]>=1.90.0",
        "litellm>=1.50.0",
        "pydantic>=2.0.0",
        "python-dotenv>=1.0.0",
        "pyyaml>=6.0",
    ]

    if config.resource_name:
        logger.info("  Updating: %s", config.resource_name)
        existing = agent_engines.get(config.resource_name)
        remote_agent = existing.update(
            agent_engine=root_agent, requirements=requirements
        )
    else:
        logger.info("  Creating new Agent Engine resource...")
        remote_agent = agent_engines.create(
            agent_engine=root_agent,
            requirements=requirements,
            display_name=config.agent_display_name,
            gcs_dir_name=config.staging_bucket,
        )

    resource_name = remote_agent.resource_name
    logger.info("Deployed: %s", resource_name)

    Path(".agent_engine_resource").write_text(resource_name + "\n")

    logger.info("Running smoke test...")
    response = remote_agent.query(input="ping")  # type: ignore[attr-defined]
    if not response:
        logger.error("Smoke test returned an empty response.")
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
