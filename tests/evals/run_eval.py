#!/usr/bin/env python3
"""Local evaluation runner with human-readable output.

Usage:
    uv run python tests/evals/run_eval.py

This is a convenience wrapper around `make eval` (npx promptfoo).
Use it for quick local runs without needing Node.js promptfoo output parsing.
"""

import os
import subprocess
import sys
from pathlib import Path

CONFIG = Path(__file__).parent / "promptfoo.yaml"


def main() -> None:
    print("Running promptfoo evaluation...")
    print(f"Config: {CONFIG}\n")

    # Get the current environment to pass to promptfoo
    env = os.environ.copy()

    # Ensure promptfoo can access the current Python environment
    env["PYTHONPATH"] = str(Path(__file__).parent.parent.parent)

    # Disable OpenTelemetry to avoid context errors in the ADK
    env["OTEL_SDK_DISABLED"] = "true"

    result = subprocess.run(
        # -j 2 matches eval.yml. promptfoo defaults to 4, which runs enough parallel
        # agent and grader calls to trip Vertex quota; the model then answers a
        # benign prompt with a "try again later" string and the suite fails for a
        # reason that has nothing to do with the agent.
        [
            "npx",
            "--yes",
            "promptfoo@latest",
            "eval",
            "--config",
            str(CONFIG),
            "-j",
            "2",
        ],
        capture_output=False,
        env=env,
    )
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
