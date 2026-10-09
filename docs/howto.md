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
Postgres.

```bash
uv sync                      # installs Python 3.12 if needed, plus all dependency groups
uv run pre-commit install    # ruff, mypy, gitleaks and hygiene hooks run on every commit
cp .env.example .env         # then fill in values; .env is gitignored
```

The first `pre-commit` run builds gitleaks with Go, which takes about a minute.
After that it is cached.

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
uv run pytest                                  # about 10 seconds, including a fresh-process determinism check
uv run pytest -m "not slow"                    # skip the subprocess regeneration test
uv run pre-commit run --all-files              # every hook, on every file
uv run pre-commit run gitleaks-history --hook-stage manual --all-files   # secrets scan over full git history
```

## Start Postgres (M1)

```bash
docker compose up -d          # Postgres 16 on localhost:${POSTGRES_PORT:-5432}, values from .env
docker compose ps             # wait for "healthy"
docker compose down           # stop; add -v to delete the data volume
```

> Note: as of Milestone 1, `docker compose config` is verified, but the container
> hasn't been started in the build environment (Docker Hub rate limit). Nothing
> uses the database until Milestone 2.

---

## Planned (not yet available)

- **Ingest** (M2): load every format into `raw_records` with source locations.
- **Extract, validate, reconcile** (M3–M5): CLI commands from
  `src/pipeline/run.py`.
- **Run the app** (M6): Streamlit.
- **Run evals** (M7): the harness and where its results are saved.
