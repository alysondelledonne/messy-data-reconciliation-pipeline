# messy-data-reconciliation-pipeline

Turns messy, multi-format data (CSV, Excel, PDF, scanned PDF, text, email, Word,
JSON/logs) into one clean, deduplicated, traceable store. Claude does the
extraction, rules and confidence scores decide what needs a human, and fuzzy
matching reconciles records across sources.

Work in progress. The full write-up, with a results scorecard taken only from real
eval runs, lands in Milestone 7.

- How to set up and run: [`docs/howto.md`](docs/howto.md)
- Architecture: [`docs/architecture.md`](docs/architecture.md)
- Design decisions: [`docs/decisions.md`](docs/decisions.md)
