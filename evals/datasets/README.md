# evals/datasets
`redteam.jsonl` - hand-written synthetic strings (injection, secrets, PHI, emergency, unsafe wording, benign controls).
Lineage: authored during development of this repo; NOT a held-out set (detector patterns were tuned while writing it),
NOT clinically reviewed, small. Results on it must be read as regression coverage, not as an estimate of real-world recall.
Exclusions: no real patient text. Add a held-out, clinician-reviewed set before any real use.
