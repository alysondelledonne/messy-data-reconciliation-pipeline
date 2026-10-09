# CLAUDE.md

Context for every Claude Code session on this repo. The owner makes the design
decisions; Claude is the pair programmer. Read this file in full before starting work.

## Project brief

This is a portfolio project for Forward Deployed Engineer applications. It shows
taking a customer's messy, inconsistent data, using an LLM to extract and reconcile
it into a clean store, and shipping it as a working, tested, deployable system.

**Name:** invoice-reconciliation-pipeline

**Scenario:** Coastline Facilities Group (fictional) is a regional property-maintenance
company. Finance needs one trustworthy view of 3 years of vendor spend, but the data
lives in three sources that disagree. The question the finished system must answer:
"How much did we spend with each vendor, by month, in USD, and which invoices are
duplicated, mismatched, or unpaid?"

### Data sources

We generate all of these ourselves with a seeded script, plus ground truth for evals:

1. ~150 vendor invoice PDFs across 6+ layouts. Field labels vary ("Inv #",
   "Invoice No.", "Ref"). Some have multi-page line items, some have totals that don't
   add up, a few are duplicates, and some are in EUR or CAD. Also ~15–20 image-only
   "scanned" PDFs that are rotated and low contrast.
2. CSV export A (legacy AP system). Mixed date formats (03/04/24, 2024-04-03,
   "April 3"), amounts with $ and commas, Latin-1 encoding, trailing blank rows.
3. CSV export B (procurement system). Different schema and column names, vendor names
   spelled differently ("ACME Plumbing LLC" vs "Acme Plumbing"), missing invoice
   numbers, duplicate rows.
4. A public exchange-rate API with no key (Frankfurter / ECB rates). Use the rate on
   the invoice date; handle weekends, caching, and API failures.

### Pipeline stages

Ingest → Extract (Claude) → Validate → Reconcile → Postgres → Streamlit app

- **Ingest:** load PDFs and CSVs into a `raw_records` table, normalizing encodings,
  dates, and amounts. Every record keeps its source file and row for traceability.
- **Extract:** Anthropic Python SDK with structured output. Pass a Pydantic `Invoice`
  model's JSON schema as a tool. Return per-field confidence and the evidence text
  used. Text PDFs send extracted text; image-only PDFs send the PDF directly. Retry on
  malformed output, cache by file hash, and log tokens and cost per document. Use a
  smaller Claude model by default and escalate hard documents to a larger one. Read
  the model name from config.
- **Validate:** rule checks (line items sum to subtotal, subtotal + tax = total,
  plausible dates, known currency). Combine these with model confidence into a status
  (`auto_approved` / `needs_review` / `rejected`) plus human-readable reasons.
- **Reconcile:** normalize vendor names and fuzzy-match them with rapidfuzz into a
  canonical vendor table; close calls go to review. Deduplicate across sources, match
  PDFs to CSV rows, and convert to USD.
- **Store:** Postgres 16 with SQLAlchemy and Alembic migrations, plus an audit trail
  for human corrections.
- **Streamlit app** has three pages. *Overview:* spend by vendor and month, counts by
  status, total LLM cost. *Review queue:* PDF on the left, extracted fields and flag
  reasons on the right, approve or correct. *Exceptions:* duplicates, unmatched
  invoices, total mismatches.
- **Evals:** compare extractions with `ground_truth.json`. Report field accuracy per
  field and per document type (text vs scanned vs layout), review-queue precision,
  missed errors (wrong records that were auto-approved), duplicate and vendor-match
  accuracy, and cost and time per 100 documents. Save each run to `evals/results/`.

### Stack

Python 3.12, uv, Pydantic v2, anthropic SDK, pdfplumber, pypdf, pandas, reportlab
(for the generator), rapidfuzz, httpx + tenacity, SQLAlchemy + Alembic, Postgres 16,
Streamlit, pytest, ruff, mypy, pre-commit, Docker Compose, GitHub Actions.

### Repo layout

- `data_gen/` — seeded generator for the messy corpus and `ground_truth.json`
- `src/pipeline/{ingest,extract,validate,reconcile,store}/` and `run.py` (CLI)
- `app/` — Streamlit
- `evals/` — harness and results history
- `tests/`
- `docs/` — architecture, `decisions.md`
- `.github/workflows/ci.yml`
- `docker-compose.yml`, `pyproject.toml`, `.env.example`

### Milestones (one per session, commit at the end of each)

1. Scaffold and messy-data generator
2. Ingestion layer and FX client
3. LLM extraction
4. Validation and confidence statuses
5. Reconciliation
6. Streamlit UI
7. Evals, CI, README case study, demo

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
- The README will be written as a client case study: problem, results scorecard,
  architecture, key decisions, what I'd do next for a real customer, how to run it,
  and a note that it was built with Claude Code as a pair programmer.
