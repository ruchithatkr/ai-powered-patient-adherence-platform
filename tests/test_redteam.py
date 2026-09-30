"""Red-team regression suite over evals/datasets/redteam.jsonl (dev set - see evals/datasets/README.md)."""

import json
from pathlib import Path

import pytest

from guardrails.checks import check_content, check_input

ROWS = [
    json.loads(line) for line in (Path(__file__).parents[1] / "evals" / "datasets" / "redteam.jsonl").read_text().splitlines()
]


def decision(row):
    if row["category"] in ("unsafe_content", "safe_content"):
        return check_content(row["text"]).decision
    results = check_input(row["text"])
    for d in ("BLOCK", "REVIEW"):
        if any(g.decision == d for g in results):
            return d
    return "PASS"


@pytest.mark.parametrize("row", ROWS, ids=[r["id"] for r in ROWS])
def test_redteam_case(row):
    assert decision(row) == row["expect"], row["text"]


def test_dataset_covers_every_category_and_has_benign_controls():
    cats = {r["category"] for r in ROWS}
    assert {"injection", "secret", "phi", "emergency", "benign", "unsafe_content", "safe_content"} <= cats
    assert sum(r["category"] == "benign" for r in ROWS) >= 10


def test_known_limitation_paraphrase_in_other_language_is_not_detected():
    """Documented limitation: detectors are English regexes. This test pins current behaviour so a change is noticed."""
    assert decision({"category": "emergency", "text": "tengo dolor en el pecho"}) == "PASS"
