# Data inventory (synthetic)
| Store | Contents | Class | Retention | Notes |
|---|---|---|---|---|
| Audit log `var/audit.jsonl` | hashed inputs/outputs, ids, statuses | pseudonymous metadata | append-only, retained | no free text/identifiers; hash-chained |
| Workflow store (memory) | WorkflowState incl. plan text | sensitive | TTL 72h | encrypted store in production |
| Long-term memory | consented intervention outcomes | sensitive | 365d (policy) | namespaced tenant+patient |
| Review tasks | evidence packets | sensitive | until deletion | purged on patient deletion |
| Synthetic backends | seeded P-001..P-007 | synthetic | process lifetime | |
