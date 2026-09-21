"""Cross-module wiring checks: the prompt registry, the agent set and the tools.

No LLM and no GCP — these assemble the real objects and assert the pieces agree
with each other. The unit tests check each agent loads *a* prompt; these check
that the registry and the agent set do not drift apart in either direction,
which is the failure you get from adding a sub-agent and forgetting its prompt
entry (or removing one and leaving the entry behind).
"""

from pathlib import Path

import yaml

from agent.agent import root_agent

PROMPTS = Path(__file__).parent.parent.parent / "prompts" / "prompts.yaml"


def _registry() -> dict:
    return yaml.safe_load(PROMPTS.read_text())["agents"]


def _agent_names() -> set[str]:
    """Every agent actually wired into the tree, root included."""
    return {root_agent.name} | {sub.name for sub in root_agent.sub_agents}


def test_every_registered_prompt_belongs_to_a_real_agent():
    """A prompts.yaml entry with no agent is dead config that looks live."""
    registered = set(_registry())
    orphaned = registered - _agent_names()

    assert not orphaned, (
        f"prompts.yaml registers {sorted(orphaned)}, which no agent uses. "
        "Either wire the agent up or drop the entry."
    )


def test_every_wired_agent_has_a_prompt_entry():
    """An agent with no entry raises at construction, but only once it is reached."""
    unregistered = _agent_names() - set(_registry())

    assert not unregistered, (
        f"{sorted(unregistered)} are wired into root_agent but absent from "
        "prompts.yaml."
    )


def test_every_registered_prompt_file_exists():
    """A missing .md surfaces as a confusing load error at agent construction."""
    root = PROMPTS.parent
    missing = [
        rel
        for entry in _registry().values()
        for rel in (entry.get("system") or []) + (entry.get("tasks") or [])
        if not (root / rel).is_file()
    ]

    assert not missing, f"prompts.yaml references missing files: {sorted(set(missing))}"


def test_root_agent_tools_are_instrumented():
    """Tools must keep their @instrument wrapper, and stay introspectable by ADK.

    `functools.wraps` sets `__wrapped__`, so this doubles as a check that the
    decorator did not break ADK's tool-schema introspection: the name and
    signature ADK reads must still be the original function's.
    """
    assert root_agent.tools, "root_agent has no tools"

    for tool in root_agent.tools:
        func = getattr(tool, "func", tool)
        assert hasattr(func, "__wrapped__"), (
            f"{getattr(func, '__name__', tool)} is not wrapped by @instrument"
        )
