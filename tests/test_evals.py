import asyncio
import hashlib
from pathlib import Path

from agents.evaluation_agent import EvaluationAgent
from app.settings import ROOT


async def test_evaluation_agent_is_reproducible_signed_and_cannot_touch_thresholds(tmp_path):
    th = ROOT / "evals" / "thresholds.yaml"
    before = hashlib.sha256(th.read_bytes()).hexdigest()
    a = EvaluationAgent(results_dir=tmp_path, signing_key="test-key", run_pytest=False)
    r1, r2 = await a.run(), await a.run()
    assert r1.ok(), r1.failures + r1.failed_cases
    assert r1.digest == r2.digest and r1.signature == r2.signature and r1.signature  # reproducible + signed
    assert hashlib.sha256(th.read_bytes()).hexdigest() == before  # thresholds are read-only for the agent
    out = a.write(r1)
    text = await asyncio.to_thread(Path(out).read_text)
    assert "NOT YET MEASURED" in text and "UNSIGNED - no human reviewer" in text and (tmp_path / f"{r1.version}.json").exists()
    assert EvaluationAgent(results_dir=tmp_path, run_pytest=False).signing_key is None


async def test_unsigned_when_no_key(tmp_path):
    r = await EvaluationAgent(results_dir=tmp_path, run_pytest=False).run()
    assert r.signature is None
