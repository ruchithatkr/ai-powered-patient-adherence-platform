"""CLI: python -m evals.run_evals  -> writes evals/results/<version>.{md,json}; exit 1 if a release gate fails."""

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agents.evaluation_agent import EvaluationAgent  # noqa: E402


def main() -> int:
    agent = EvaluationAgent(signing_key=os.environ.get("ADHERENCE_EVAL_SIGNING_KEY"))
    report = asyncio.run(agent.run())
    path = agent.write(report)
    print(path.read_text())
    return 0 if report.ok() else 1


if __name__ == "__main__":
    sys.exit(main())
