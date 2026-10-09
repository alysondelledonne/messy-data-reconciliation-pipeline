# CLAUDE.md

Context for every Claude Code session on this repo. The owner makes the design
decisions; Claude is the pair programmer. Read this file in full before starting work,
then read `docs/decisions.md` (especially "Open decisions") and the status column in
`docs/architecture.md`.

## Project brief

This is a portfolio project for Forward Deployed Engineer applications. It shows
taking messy, inconsistent data in many formats, using an LLM to extract and
reconcile it into a clean store, and shipping that as a working, tested, deployable
system.

**Name:** messy-data-reconciliation-pipeline

**What it does:** You give the system a *target schema* and a pile of messy files in
mixed formats that describe the same real-world records. It produces a single clean,
deduplicated table where every value can be traced back to its source. It also says
which records need a human to review them and why. The system is generic; it does not
tell one company's story (D-003, D-005).

**The question it must answer for any dataset:** "What are the true records, where did
each value come from, which records are duplicated or contradict each other across
sources, and which need human review?"

### Domain packs (D-004)

The engine is driven by the target schema. Everything specific to one kind of data
lives in a *domain pack*. Each pack holds:

- the target Pydantic model, with field descriptions and the label/column aliases the
  pack expects;
- the validation rules for that domain;
- the entity keys used for fuzzy matching and dedupe;
- a seeded synthetic data generator and its ground truth.

We ship a few packs (which ones is in Open decisions) to prove the engine really is
generic. Adding a pack should require no changes to the engine.

### Input formats (D-006)

| Format | Mess to plant |
|---|---|
| CSV | Mixed encodings (Latin-1/UTF-8), mixed date formats, `$1,234.50` amounts, trailing blank rows, duplicate rows, schemas that differ between sources |
| Excel (.xlsx) | Multiple sheets, header not in row 1, merged cells, totals rows, dates stored as serial numbers, notes in stray cells |
| PDF, text layer | Varied layouts and label wording, tables that run across pages, internal totals that don't add up |
| PDF, scanned | Image-only pages, rotated, low contrast, noise |
| TXT / MD | Free-form notes and memos with the record buried in prose |
| EML | Headers, quoted reply chains, signatures, record data in the body and/or attachments |
| DOCX | Tables and paragraphs, inconsistent styling |
| JSON / JSONL / logs | Keys named inconsistently, nested vs flat shapes, missing fields, log-line formats |

All data is synthetic. We generate it with a seeded script, together with ground
truth for the evals. Within a pack, the same real-world record is planted across
several sources with variant spellings, conflicting values and duplicates, so that
reconciliation can be scored.

### Pipeline stages

Ingest → Extract (Claude) → Validate → Reconcile → Postgres → Streamlit app

- **Ingest:** one adapter per format loads files into a `raw_records` table and
  normalizes encodings, dates and amounts. Every record keeps its *source location*:
  file plus row, sheet/cell, page, line, or email part.
- **Extract:** Anthropic Python SDK with structured output. The domain pack's
  Pydantic model is passed to the model as a tool via its JSON schema. Each field
  comes back with a confidence score and the evidence text used. Text sources send
  text; scanned PDFs send the PDF directly. Retry on malformed output, cache by
  content hash, and log tokens and cost per document. Use a smaller Claude model by
  default and escalate hard documents to a larger one. Read model names from config.
  How structured sources (CSV/Excel/JSON) differ from unstructured ones will be
  decided at Milestone 3.
- **Validate:** each domain pack provides rule checks; the engine combines them with
  model confidence into a status (`auto_approved` / `needs_review` / `rejected`) plus
  human-readable reasons.
- **Reconcile:** normalize entity names and fuzzy-match them with rapidfuzz into
  canonical entity tables; close calls go to review. Deduplicate across sources and
  link records that describe the same thing across sources. Report conflicts field by
  field.
- **Store:** Postgres 16 with SQLAlchemy and Alembic migrations, plus an audit trail
  for human corrections.
- **Streamlit app** has three pages:
  - *Overview:* record counts by pack, source format and status, plus total LLM cost.
  - *Review queue:* source preview on the left (PDF, image, email, sheet), extracted
    fields and flag reasons on the right; approve or correct.
  - *Exceptions:* duplicates, unmatched records, cross-source conflicts and rule
    failures.
- **Evals:** compare results with each pack's ground truth. Report:
  - field accuracy per field, per pack and per source format (including text vs
    scanned PDF);
  - review-queue precision;
  - missed errors (wrong records that were auto-approved);
  - dedupe and entity-match accuracy;
  - cost and time per 100 documents.

  Save each run to `evals/results/`.

Removed from the original brief: the FX conversion stage and the
Frankfurter/ECB client (D-007).

### Stack

Python 3.12, uv, Pydantic v2, anthropic SDK, pdfplumber, pypdf, openpyxl,
python-docx, pandas, reportlab + Pillow (for the generator), rapidfuzz, tenacity,
SQLAlchemy + Alembic, Postgres 16, Streamlit, pytest, ruff, mypy, pre-commit, Docker
Compose, GitHub Actions. Emails use the standard library's `email` package.

### Repo layout

- `data_gen/` — seeded generator: shared format writers plus one generator per
  domain pack; writes the corpus and ground truth
- `src/pipeline/{ingest,extract,validate,reconcile,store}/` and `run.py` (CLI)
- `src/pipeline/domains/<pack>/` — the pack's schema, aliases, rules and entity keys
- `app/` — Streamlit
- `evals/` — harness and results history
- `tests/`
- `docs/` — `architecture.md`, `decisions.md`, `howto.md`
- `.github/workflows/ci.yml`
- `docker-compose.yml`, `pyproject.toml`, `.env.example`

### Milestones (one per session, commit at the end of each)

1. Scaffold and messy-data generator (all formats, first domain packs, ground truth)
2. Ingestion adapters for every format, `raw_records`, first Alembic migration
3. LLM extraction, driven by the schema
4. Validation and confidence statuses
5. Reconciliation (entity matching, dedupe, linking across sources, conflicts)
6. Streamlit UI
7. Evals, CI, README write-up, demo

## Working rules

- Plan before coding at each milestone. Explain the approach and any real tradeoffs,
  then wait for the owner's approval. Ask when a decision is the owner's to make.
- Small, reviewable steps. After each milestone, summarize what changed, run the
  tests, and propose a commit message.
- For every real design choice, add a short entry to `docs/decisions.md`: the choice,
  the alternatives, and why.
- Keep three living docs current as part of every step, not as a cleanup at the end.
  A milestone is not done until all three match the code:
  - `docs/decisions.md`: major decisions (numbered `D-NNN`, never deleted, superseded
    when reversed), plus an "Open decisions" list of questions waiting on the owner.
  - `docs/architecture.md`: the system as it is now (diagram, components with a
    built/planned status, data model, cross-cutting concerns).
  - `docs/howto.md`: commands that actually work today for setup, running, testing
    and operating, each tagged with the milestone that added it.
- Tests for every module. CI uses a mocked LLM client, so no API key is needed in CI.
- Never commit secrets. Read `ANTHROPIC_API_KEY` and `DATABASE_URL` from `.env`, keep
  `.env` in `.gitignore`, and add a secret-scanning pre-commit hook.
- Only synthetic data and public APIs. No real company, client, or employer names,
  code, or data.
- Never invent results. README metrics come only from actual eval runs.
- Keep cost low: cache LLM calls, and use the smaller model unless escalation is
  needed.
- The README is a project write-up, not a client story (D-005). It covers: the
  problem (messy multi-format data), a results scorecard from real eval runs, the
  architecture, key decisions, what I'd do next for a real deployment, how to run
  it, and a note that it was built with Claude Code as a pair programmer.
