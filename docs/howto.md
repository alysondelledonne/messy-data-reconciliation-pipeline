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
docker compose up -d postgres # Postgres 16 on localhost:${POSTGRES_PORT:-5432}, values from .env
docker compose ps             # wait for "healthy"
docker compose down           # stop; add -v to delete the data volume
```

`docker compose up -d` without a service name also builds and starts the review app
(M6, see "Run the review app" below). Expect `docker compose ps` to show `(healthy)`
after about 5–10 seconds, and `psql`
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

## Validate extracted records (M4)

```bash
uv run python -m pipeline.run validate --pack invoices
uv run python -m pipeline.run validate --pack contacts
```
- No API calls; it takes seconds.
- It prints counts per status, the most common reasons, and approved / review /
  rejected counts per source format.
- Re-run it any time, for example after a new extraction or a threshold change. It
  replaces the verdicts for the current validator version.
- The thresholds are in `.env`: `REVIEW_CONFIDENCE` (default 0.75) and
  `CONFIDENCE_THRESHOLD` (0.6, extraction escalation). Changing one creates a new
  validator version, and the old verdicts are kept for comparison.

```sql
-- Status counts
SELECT pack, status, count(*) FROM validations GROUP BY 1, 2 ORDER BY 1, 2;

-- What sends records to review, with an example message per rule
SELECT v.pack, f->>'rule' AS rule, count(*) AS records, min(f->>'message') AS example
FROM validations v, jsonb_array_elements(v.findings) f
WHERE f->>'severity' <> 'info' GROUP BY 1, 2 ORDER BY 1, 3 DESC;

-- The review queue: source location and every reason
SELECT sf.path, r.location, string_agg(f->>'message', ' | ') AS reasons
FROM validations v JOIN extracted_records x ON x.id = v.extracted_record_id
JOIN extraction_results er ON er.id = x.result_id JOIN raw_records r ON r.id = er.raw_record_id
JOIN source_files sf ON sf.id = r.source_file_id, jsonb_array_elements(v.findings) f
WHERE v.pack = 'invoices' AND v.status = 'needs_review' AND f->>'severity' <> 'info'
GROUP BY v.id, sf.path, r.location ORDER BY sf.path LIMIT 20;
```

Expect a large share of invoice reviews to come from gaps in a single source: a
legacy CSV row with no currency marker, a procurement row without an invoice number,
an ambiguous `04/03/24` date. Reconciliation (M5) fills many of these from other
sources for the same invoice (D-029).

## Reconcile into golden records (M5)

Run after `validate`, which reconciliation needs so it can leave out rejected records.
On an existing database, apply migration 0004 first (`relation "match_decisions" does
not exist` means you skipped it):

```bash
uv run python -m pipeline.run db upgrade
uv run python -m pipeline.run reconcile --pack invoices
uv run python -m pipeline.run reconcile --pack contacts
```
- It takes a few seconds. It rebuilds the pack's entities and golden records from
  scratch each time, so re-run it after any extract or validate.
- Close name matches (for example "Bluewater Holdings" vs "Bluewater Holding Group")
  go to Claude Haiku. Expect a handful of calls and a fraction of a cent. Answers
  are cached and stored in `match_decisions`, so a re-run asks nothing new.
- `--no-llm`, `MATCH_LLM=false`, or no API key: close matches stay `pending`, the
  names are kept apart, and their golden records go to review.
- Tuning (in `.env`): `MATCH_AUTO` (92), `MATCH_REVIEW` (80), `MATCH_LLM_CONFIDENCE`
  (0.8).
- The summary shows golden records, entities, close calls and their cost, link
  reasons, statuses, conflicts per field and review reasons.

If you regenerated the corpus (generator 1.3.0 changed the contacts files), reset and
re-run from ingest as in "Start from a clean, current corpus" above. Re-extracting
contacts makes new API calls for the changed files only; the invoices files are
unchanged and come from the cache.

### Check against ground truth (no API calls)
```bash
uv run python -m evals.reconcile_check --pack invoices
uv run python -m evals.reconcile_check --pack contacts --show 20
```
Prints how many golden records are pure (one true record each), how many true
records landed in exactly one golden record, entity pairs merged correctly or
wrongly, and golden field accuracy against the clean truth.

### Look at the results
```sql
-- Golden records by status
SELECT pack, status, count(*), sum((source_count > 1)::int) AS multi_source
FROM golden_records GROUP BY 1, 2 ORDER BY 1, 2;

-- Entities and the spellings merged into them
SELECT pack, canonical_name, jsonb_array_length(aliases) AS spellings, aliases
FROM entities ORDER BY pack, spellings DESC;

-- Cross-source conflicts, field by field
SELECT g.pack, c->>'field' AS field, c->>'chosen' AS chosen, c->>'rule' AS rule,
       c->'alternatives' AS lost
FROM golden_records g, jsonb_array_elements(g.conflicts) c ORDER BY 1, 2 LIMIT 30;

-- Why golden records need review
SELECT pack, f->>'rule' AS rule, count(*), min(f->>'message') AS example
FROM golden_records, jsonb_array_elements(findings) f
WHERE f->>'severity' = 'review' GROUP BY 1, 2 ORDER BY 1, 3 DESC;

-- Where one golden record's values came from
SELECT g.id, g.fields->>'invoice_number' AS number, l.reason, sf.path, r.location
FROM golden_records g JOIN record_links l ON l.golden_id = g.id
JOIN extracted_records x ON x.id = l.extracted_record_id
JOIN extraction_results er ON er.id = x.result_id JOIN raw_records r ON r.id = er.raw_record_id
JOIN source_files sf ON sf.id = r.source_file_id
WHERE g.pack = 'invoices' AND g.source_count > 2 ORDER BY g.id LIMIT 20;

-- Close name matches and who decided them
SELECT pack, a, b, decision, decided_by, confidence, reason FROM match_decisions ORDER BY 1, 2;
```

### Decide a close match yourself
Until the review UI (M6) exists, update the row directly. Names are stored normalized
(legal suffixes such as "Group" dropped), with `a` sorting before `b`. A `human:`
decision is never overwritten by the model, and the next `reconcile` uses it:

```sql
UPDATE match_decisions SET decision = 'different', decided_by = 'human:me'
WHERE pack = 'contacts' AND a = 'bluewater holding' AND b = 'bluewater holdings';
```

## Run the review app (M6)

Needs reconciled data (see above). Two ways to run it.

**From your checkout:**

```bash
uv run python -m pipeline.run db upgrade
uv run streamlit run app/streamlit_app.py
```

Then open http://localhost:8501.

**With Docker** (Postgres and the app together; the app applies migrations itself):

```bash
docker compose up -d --build
```

Then open http://localhost:8501. The container reads source files from
`./data/generated` (mounted read-only at `/data`). Change the port with `APP_PORT` in
`.env`.

Using it:
1. Enter your name in the sidebar. It's recorded with every decision, as `human:<name>`.
2. **Review queue → Records** lists golden records that need review. The source is on
   the left: PDF pages, the email, the spreadsheet rows around the record, or the text
   with the evidence highlighted. The golden record is on the right, with why each
   value won and what lost.
   - **Approve** marks the golden record `approved`.
   - **Correct a field** saves a value for one field (empty clears it; JSON for line
     items). The record is re-checked against the pack rules right away.
3. **Review queue → Name matches** lists company or vendor names the rules and the
   model couldn't settle. Choose *Same organization* or *Different*, then **Re-run
   reconcile now** (it makes no model calls).
4. **Exceptions** has duplicates, unmatched records, conflicts, rule failures and the
   audit trail, each downloadable as CSV.

Approvals and corrections survive re-running `reconcile`, which re-applies them. If
the sources behind a reviewed record change (for example, after re-extraction links
another document to it), the record comes back to the queue as `review_orphaned`, with
a button to re-apply the earlier corrections.

If previews say "Source file not found", set `FILES_ROOT` in `.env` to the corpus
folder (for example `data/generated`).

```sql
-- Every human decision, newest first
SELECT at, actor, action, pack, target, field, old, new, note
FROM review_events ORDER BY at DESC LIMIT 20;

-- Stored corrections and approvals
SELECT pack, golden_key, field, value, reviewer FROM golden_overrides;
SELECT pack, golden_key, reviewer, note FROM golden_reviews;
```

---

## Planned (not yet available)

- **Run evals** (M7): the harness and where its results are saved.
