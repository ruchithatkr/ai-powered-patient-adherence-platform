#!/usr/bin/env python3
"""Stop hook: run the test suite and print unresolved risks."""
import subprocess, sys
from pathlib import Path
root = Path(__file__).resolve().parents[1]
r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"], cwd=root, capture_output=True, text=True)
print((r.stdout.strip().splitlines() or ["no output"])[-1])
print("Unresolved risks: see README 'Limitations'. Evaluation results remain NOT YET MEASURED for model metrics.")
sys.exit(0 if r.returncode == 0 else 2)
