"""Structural checks on the promptfoo eval config — no model calls, no network.

The golden set shipped in this repo for months without ever being loaded: its cases were
hand-copied into promptfoo.yaml instead, so editing the file changed nothing and the two
copies drifted. These tests fail if that regresses.
"""

import json
from pathlib import Path

import yaml

EVALS_DIR = Path(__file__).resolve().parents[1] / "evals"
CONFIG_PATH = EVALS_DIR / "promptfoo.yaml"
GOLDEN_SET_PATH = EVALS_DIR / "datasets" / "golden_set.jsonl"


def load_config() -> dict:
    return yaml.safe_load(CONFIG_PATH.read_text())


def test_golden_set_is_actually_loaded():
    """The bug this guards: a dataset that exists, is documented, and is never read."""
    tests = load_config()["tests"]
    file_refs = [entry for entry in tests if isinstance(entry, str)]

    assert any("golden_set.jsonl" in ref for ref in file_refs), (
        "promptfoo.yaml must load datasets/golden_set.jsonl; "
        f"only these file refs were found: {file_refs}"
    )


def test_every_referenced_dataset_exists():
    """A file:// ref to a missing path makes promptfoo fail at run time, which in CI
    reads as a failing eval rather than a broken config."""
    tests = load_config()["tests"]

    for entry in tests:
        if isinstance(entry, str) and entry.startswith("file://"):
            referenced = EVALS_DIR / entry.removeprefix("file://")
            assert referenced.is_file(), f"{entry} does not exist"


def test_golden_set_rows_are_valid_test_cases():
    rows = [
        json.loads(line)
        for line in GOLDEN_SET_PATH.read_text().splitlines()
        if line.strip()
    ]

    assert rows, "golden_set.jsonl is empty"
    for row in rows:
        assert "prompt" in row["vars"]
        assert row["assert"], f"{row['description']!r} asserts nothing"


def test_rubric_assertions_have_a_grader_that_is_not_openai():
    """`llm-rubric` is graded by a model. promptfoo defaults that grader to OpenAI, which
    this project never configures, so an unset provider means every rubric case errors."""
    config = load_config()
    rows = [
        json.loads(line)
        for line in GOLDEN_SET_PATH.read_text().splitlines()
        if line.strip()
    ]
    uses_rubric = any(
        assertion["type"] == "llm-rubric" for row in rows for assertion in row["assert"]
    )

    if not uses_rubric:
        return

    provider = config["defaultTest"]["options"]["provider"]
    assert not provider.startswith("openai:"), (
        f"grader provider is {provider!r}; this project has no OpenAI credentials"
    )
