from pathlib import Path

import yaml


def load_prompt(agent_name: str) -> str:
    """Load and concatenate prompt .md files for a named agent from prompts/prompts.yaml."""
    prompts_dir = Path(__file__).parent.parent / "prompts"
    registry_path = prompts_dir / "prompts.yaml"

    with open(registry_path) as f:
        registry = yaml.safe_load(f)

    agent_config = registry.get("agents", {}).get(agent_name, {})
    parts: list[str] = []

    for category in ("system", "tasks"):
        for file_path in agent_config.get(category, []):
            full_path = prompts_dir / file_path
            parts.append(full_path.read_text().strip())

    if not parts:
        raise ValueError(
            f"No prompts found for agent '{agent_name}' in {registry_path}. "
            "Add an entry under agents: in prompts/prompts.yaml."
        )

    return "\n\n---\n\n".join(parts)
