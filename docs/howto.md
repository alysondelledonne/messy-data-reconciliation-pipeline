# How-to

Step-by-step commands for setting up, running, testing and operating the system.
Each section says which milestone it was added in. Only document commands that
actually work in the repo today. Planned steps are listed at the bottom and marked
as not yet available.

## Start a new working session with Claude Code (M0)

1. Read `CLAUDE.md` (the brief and working rules) and the latest entries in
   `docs/decisions.md`.
2. Check the status column in `docs/architecture.md` to see what is built.
3. Plan the current milestone, get the owner's approval, then build.
4. Before committing, update all three docs, run the checks below and propose a
   commit message.

## Record a design decision (M0)

1. Copy the template at the top of `docs/decisions.md`.
2. Give it the next `D-NNN` number and fill in context, choice, alternatives, why and
   consequences.
3. If it reverses an earlier decision, mark the old entry
   `Superseded by D-NNN` instead of deleting it.

## Set up a development machine (M1)

Prerequisites: [uv](https://docs.astral.sh/uv/) and Git. Docker is needed only for
Postgres. You don't need to install Python yourself; uv fetches 3.12.

Install uv if `uv --version` fails, then open a new terminal:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh        # macOS / Linux (or: brew install uv)
# Windows (PowerShell):
# powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Then:

```bash
uv sync                      # installs Python 3.12 if needed, plus all dependency groups
uv run pre-commit install    # ruff, mypy, gitleaks and hygiene hooks run on every commit
cp .env.example .env         # then fill in values; .env is gitignored
```

The first `pre-commit` run builds gitleaks with Go, which takes about a minute.
After that it is cached.

> **macOS: keep the repo out of iCloud-synced folders** (Desktop, Documents). iCloud
> marks files there as hidden, and Python 3.12 skips hidden `.pth` files. That
> breaks the editable install, so you get `ModuleNotFoundError: No module named
> 'pipeline'`. To check, run
> `ls -lO .venv/lib/python3.12/site-packages/*.pth` and look for `hidden`.
> Fix: move the repo, e.g. to `~/code`, then `rm -rf .venv && uv sync`. Or, after
> every sync, run `chflags nohidden .venv/lib/python3.12/site-packages/*.pth`.

## Generate the messy corpus (M1)

```bash
uv run python -m data_gen                       # seed 42 -> data/generated/
uv run python -m data_gen --seed 7 --out /tmp/c # another seed, another directory
uv run python -m data_gen --packs contacts      # a single pack
```

- It takes about 4 seconds and writes about 300 files (about 5 MB).
- The same seed always produces byte-identical files. To confirm, compare
  `data/generated/manifest.json` between runs; `sha256sum` should match.
- The command replaces its output directory, but refuses to wipe a non-empty
  directory that has no `manifest.json`.

### Look at the mess

| What | Where | What to notice |
|---|---|---|
| Vendor PDFs | `data/generated/invoices/pdf/*.pdf` | 7 layouts; `scan_*.pdf` are rotated, low-contrast images with no text; `* (1).pdf` and `*_reissued.pdf` are duplicates |
| Legacy AP export | `data/generated/invoices/ap_register.csv` | Open it as UTF-8 and the accents break (it's Latin-1). Also: report preamble, `04/03/24` vs `2024-04-03` vs `April 3`, `$1,234.50`, uppercase vendor names cut off, blank rows at the end |
| Procurement export | `data/generated/invoices/procurement_export.csv` | Different columns, vendor aliases ("Tidewtaer Pest Control"), empty invoice refs, duplicate rows |
| AP aging report | `data/generated/invoices/ap_aging.xlsx` | Header on row 5; supplier only on the first row of each group; subtotal rows; some dates are bare numbers |
| Emails | `data/generated/contacts/email/*.eml` | Signatures, `> ` quoted replies naming *other* people, vCard attachments, HTML parts |
| Word docs | `data/generated/contacts/docx/*.docx` | Contact tables, attendee lists in one paragraph, memo prose |
| Notes | `data/generated/contacts/notes/*` | Contacts buried in sentences or bullets; partial names like "Dana W." |
| JSONL / logs | `data/generated/contacts/exports/` | Three JSON shapes, malformed lines, three log formats mixed with noise |
| Ground truth | `data/generated/<pack>/ground_truth.json` | The true value of every record, and where each one appears |

Example: how did one invoice end up in each source?

```bash
uv run python - <<'EOF'
import json
gt = json.load(open("data/generated/invoices/ground_truth.json"))
r = next(r for r in gt["records"] if len(r["occurrences"]) >= 3)
print(json.dumps(r["truth"] | {"line_items": "..."}, indent=2))
for o in r["occurrences"]:
    print(o["format"], o["source"], o["location"], o["defects"])
EOF
```

## Run the checks (M1)

These are the same checks CI runs:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest                                  # about 20 seconds; DB tests need Postgres up (see below)
uv run pytest -m "not slow"                    # skip the subprocess regeneration test
uv run pre-commit run --all-files              # every hook, on every file
uv run pre-commit run gitleaks-history --hook-stage manual --all-files   # secrets scan over full git history
```

## Start Postgres (M1)

Needs Docker with Compose v2. On macOS, install [Docker Desktop](https://www.docker.com/products/docker-desktop/)
(free for personal use) or `brew install --cask docker`, and start it once so the
`docker` command is available. [OrbStack](https://orbstack.dev/) or Colima also work.

```bash
docker compose up -d          # Postgres 16 on localhost:${POSTGRES_PORT:-5432}, values from .env
docker compose ps             # wait for "healthy"
docker compose down           # stop; add -v to delete the data volume
```

Expect `docker compose ps` to show `(healthy)` after about 5–10 seconds, and `psql`
(`docker compose exec postgres psql -U pipeline -d pipeline -c 'select version();'`)
to print `PostgreSQL 16.x`.

## Create the database tables (M2)

With Postgres running (see above):

```bash
uv run python -m pipeline.run db upgrade       # apply all migrations
uv run python -m pipeline.run db current       # show the current revision (0001)
uv run python -m pipeline.run db downgrade     # undo one revision; "base" undoes all
```

`DATABASE_URL` comes from `.env`. Every schema change ships as a new file in
`migrations/versions/`, and a test fails if the models and migrations disagree (D-022).

## Ingest files (M2)

```bash
uv run python -m pipeline.run ingest --pack invoices --dry-run data/generated/invoices
uv run python -m pipeline.run ingest --pack invoices data/generated/invoices
uv run python -m pipeline.run ingest --pack contacts data/generated/contacts
```

- `--dry-run` parses everything and prints counts by format and flag, without
  touching the database.
- Re-running is safe: unchanged files are skipped (`159 unchanged (skipped)`). A
  changed file is stored as a new version.
- `ground_truth.json`, `manifest.json` and dotfiles are skipped. Add more patterns
  with `--exclude '*.tmp'`.
- Unsupported or broken files are reported and recorded with `status`, and the run
  continues. The exit code is 1 if any file errored.

Expected for the seed-42 corpus: invoices 159 files / 718 records, contacts 143
files / 707 records.

### Look at what was ingested

```bash
docker compose exec postgres psql -U pipeline -d pipeline
```

```sql
-- Records per format
SELECT sf.format, count(*) FROM raw_records r
JOIN source_files sf ON sf.id = r.source_file_id GROUP BY 1 ORDER BY 1;

-- The legacy AP export: original vs normalized values, with source row
SELECT r.location, r.raw->>'Inv Date' AS raw_date, r.normalized->'Inv Date' AS inv_date,
       r.raw->>'Amount' AS raw_amount, r.normalized->'Amount' AS amount
FROM raw_records r JOIN source_files sf ON sf.id = r.source_file_id
WHERE sf.path = 'ap_register.csv' ORDER BY r.id LIMIT 10;

-- What was inferred or needs attention
SELECT r.flags, count(*) FROM raw_records r GROUP BY 1 ORDER BY 2 DESC;

-- Scanned PDFs waiting for vision extraction
SELECT sf.path FROM raw_records r JOIN source_files sf ON sf.id = r.source_file_id
WHERE r.flags ? 'needs_vision';

-- Exact duplicate files
SELECT path, meta->'identical_to' FROM source_files WHERE meta ? 'identical_to';
```

### Start over

```bash
uv run python -m pipeline.run db downgrade base && uv run python -m pipeline.run db upgrade
```

## Database tests (M2)

- `uv run pytest` runs the DB tests against a separate `pipeline_test` database,
  dropped, recreated and migrated once per session. Each test is rolled back.
- If Postgres isn't reachable, those tests are **skipped** and pytest says why. In
  CI, `REQUIRE_DB=1` makes them fail instead (D-017).
- To point them somewhere else, set `TEST_DATABASE_URL`.

## Extract records with Claude (M3)

### 1. Add your API key
Put `ANTHROPIC_API_KEY=sk-ant-...` in `.env`. It's gitignored, and gitleaks blocks
committing keys. CI never needs a key; it uses a fake client (D-027).

If calls fail with `This API key is not scoped to a workspace ... anthropic-workspace-id`
(check with `SELECT status, error FROM llm_calls LIMIT 5;`), there are two fixes:
- Create a key inside a workspace in the Console, under Settings → API Keys, and use
  that key instead.
- Or keep your key and add `ANTHROPIC_WORKSPACE_ID=wrkspc_...` to `.env`. The ID is on
  the Console's Workspaces page. The pipeline then sends it as the
  `anthropic-workspace-id` header.

Failed calls aren't cached, so re-running `extract` retries them.

### 2. Start from a clean, current corpus
If you regenerated the corpus since your last ingest (generator 1.1.0 fixed
ground-truth bugs and renamed some contacts files), reset the database, because
ingest doesn't track deleted files:

```bash
uv run python -m data_gen
uv run python -m pipeline.run db downgrade base
uv run python -m pipeline.run db upgrade
uv run python -m pipeline.run ingest --pack invoices data/generated/invoices
uv run python -m pipeline.run ingest --pack contacts data/generated/contacts
```

### 3. Estimate before spending (no API calls)
```bash
uv run python -m pipeline.run extract --pack invoices --estimate
uv run python -m pipeline.run extract --pack contacts --estimate
```
This prints the number of calls, a token range and a cost range for Haiku plus
escalations. It's a rough guide; real numbers land in `llm_calls`.

### 4. A small live run first
```bash
uv run python -m pipeline.run extract --pack invoices --limit 5
```
- Mapped rows (CSV, XLSX, JSON, DOCX tables) are always processed. They cost one
  call per header layout.
- `--limit` caps the documents that each need their own call.
- The run prints results by status and method, escalations, API calls, cache hits
  and the cost of this run.

### 5. The full run
```bash
uv run python -m pipeline.run extract --pack invoices
uv run python -m pipeline.run extract --pack contacts
```
- Re-running is free: finished raw records are skipped, and any repeated request is
  answered from `llm_calls`.
- Scanned PDFs are read from disk. If the corpus moved since ingest, pass
  `--files-root data/generated/invoices`.
- Model choice and tuning are in `.env`: `EXTRACT_MODEL`, `ESCALATION_MODEL`,
  `EXTRACT_EFFORT`, `CONFIDENCE_THRESHOLD`, `EXTRACT_WORKERS` (see `.env.example`).

### Inspect results and cost
```bash
docker compose exec postgres psql -U pipeline -d pipeline
```

```sql
-- Spend by pack, model and purpose
SELECT pack, model_served, purpose, count(*) AS calls, sum(input_tokens) AS input_tokens,
       sum(output_tokens) AS output_tokens, round(sum(cost_usd), 4) AS usd
FROM llm_calls GROUP BY 1, 2, 3 ORDER BY 1, 2, 3;

-- Outcomes and escalations
SELECT er.pack, er.method, er.status, count(*), sum(er.escalated::int) AS escalated
FROM extraction_results er GROUP BY 1, 2, 3 ORDER BY 1, 2, 3;

-- Why documents were escalated
SELECT sf.path, er.model, er.reasons
FROM extraction_results er JOIN raw_records r ON r.id = er.raw_record_id
JOIN source_files sf ON sf.id = r.source_file_id
WHERE er.escalated ORDER BY sf.path LIMIT 20;

-- Extracted invoices with evidence
SELECT sf.path, x.fields->>'vendor_name' AS vendor, x.fields->>'invoice_number' AS number,
       x.fields->>'total' AS total, x.field_meta->'total'->>'evidence' AS total_evidence
FROM extracted_records x JOIN extraction_results er ON er.id = x.result_id
JOIN raw_records r ON r.id = er.raw_record_id JOIN source_files sf ON sf.id = r.source_file_id
WHERE er.pack = 'invoices' AND er.method = 'llm' LIMIT 10;

-- Weakest values (what the review queue will show in M4/M6)
SELECT sf.path, f.key AS field, f.value->>'final_confidence' AS conf, f.value->'flags' AS flags
FROM extracted_records x JOIN extraction_results er ON er.id = x.result_id
JOIN raw_records r ON r.id = er.raw_record_id JOIN source_files sf ON sf.id = r.source_file_id,
     jsonb_each(x.field_meta) f
WHERE (f.value->>'final_confidence')::numeric < 0.6 AND x.fields->>f.key IS NOT NULL
ORDER BY conf LIMIT 20;

-- The column mappings Claude proposed
SELECT signature, columns, mapping->'model' AS model, mapping->'mappings' FROM column_mappings;
```

### Check accuracy against ground truth (no API calls)
```bash
uv run python -m evals.spot_check --pack invoices
uv run python -m evals.spot_check --pack contacts --show 20
```
Scores the records extracted so far, field by field, against what each source
actually states, separately for LLM documents and mapped rows, and prints example
mismatches with file and location. A quick sanity check; the full eval is Milestone 7.

### Re-extract
- **Clear results but keep the cache:** `DELETE FROM extraction_results;`. The next
  run rebuilds everything from `llm_calls` at no cost.
- **Pay for fresh answers:** bump `PROMPT_VERSION` in `src/pipeline/extract/prompts.py`.
  Do this whenever the prompts change meaning.

---

## Planned (not yet available)

- **Validate, reconcile** (M4–M5): CLI commands from `src/pipeline/run.py`.
- **Run the app** (M6): Streamlit.
- **Run evals** (M7): the harness and where its results are saved.
