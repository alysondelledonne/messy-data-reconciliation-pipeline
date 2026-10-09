# Decision log

Every real design choice gets an entry here: what we chose, what else we considered,
and why. Newest entries go at the bottom. Entries are never deleted. If a decision is
reversed, add a new entry that supersedes the old one and mark the old one
`Superseded by D-NNN`.

**Status values:** `Proposed` (waiting on the owner) · `Accepted` · `Superseded`

Entry template:

```markdown
## D-NNN: Short title
- **Date:** YYYY-MM-DD · **Milestone:** N · **Status:** Accepted
- **Context:** the problem or constraint that forced a choice.
- **Choice:** what we did.
- **Alternatives:** what else we considered.
- **Why:** the reasoning and the tradeoff we accepted.
- **Consequences:** what this commits us to later, if anything.
```

---

## D-000: Baseline stack and pipeline shape
- **Date:** 2026-10-09 · **Milestone:** 0 · **Status:** Accepted (owner's brief)
- **Context:** A portfolio project has to show the whole path from messy customer
  data to a deployed system, using a stack that customers actually run.
- **Choice:** Ingest → Extract (Claude) → Validate → Reconcile → Postgres →
  Streamlit, built with Python 3.12, uv, Pydantic v2, the anthropic SDK, SQLAlchemy
  + Alembic, Postgres 16, Docker Compose and GitHub Actions. The full list is in
  `CLAUDE.md`.
- **Alternatives:** Not evaluated; the owner set this in the brief.
- **Why:** Every stage can be tested on its own, the stack is mainstream, and the
  stage boundaries match the questions an FDE gets asked in a customer engagement.
- **Consequences:** Later decisions work inside this stack. Changing any part of it
  needs a new entry here.

## D-001: Project context and docs live in the repo
- **Date:** 2026-10-09 · **Milestone:** 0 · **Status:** Accepted (owner)
- **Context:** Work happens over several Claude Code sessions, and each one starts
  with no memory of the last. Reviewers of the portfolio should be able to follow
  how the system was built.
- **Choice:** `CLAUDE.md` holds the brief and working rules. Three living docs are
  kept up to date as part of every step: `docs/decisions.md` (this file),
  `docs/architecture.md` (how the system fits together right now) and
  `docs/howto.md` (commands to set up, run, test and operate it).
- **Alternatives:** An external wiki such as Notion, or only commit messages and PR
  descriptions.
- **Why:** Docs in the repo get versioned with the code and reviewed in the same
  diff. Anyone looking at the GitHub repo can read them without extra access.
- **Consequences:** A milestone isn't done until all three docs match the code.

## D-002: Generated corpus is gitignored
- **Date:** 2026-10-09 · **Milestone:** 1 · **Status:** Accepted (owner)
- **Context:** The generator writes many binary files (PDFs, and possibly scanned
  images and spreadsheets). Committing them makes every regeneration a large, noisy
  diff.
- **Choice:** `data/generated/` is in `.gitignore`. A single seeded command rebuilds
  the corpus, and a test asserts that the same seed produces the same file hashes in
  `manifest.json`.
- **Alternatives:** Commit the full corpus so reviewers can browse it on GitHub
  without running anything, or commit a small hand-picked sample.
- **Why:** The command is the source of truth, and git history stays clean. The
  determinism test is what makes this safe.
- **Consequences:** CI and tests must regenerate the data they need, so the
  generator has to stay fast. The README must explain how to regenerate the corpus.

## D-003: Re-scope from one company's invoices to generic messy data
- **Date:** 2026-10-09 · **Milestone:** 1 · **Status:** Accepted (owner); details
  open
- **Context:** The original brief was scoped to a single fictional company
  (Coastline Facilities Group) and a single document type (vendor invoices). The
  owner wants the project to show broader skill: handling any messy customer data.
- **Choice:** Turn the pipeline into a generic messy-data system. Inputs can include
  CSV, Excel, PDF and plain-text documents. The scope details are listed under
  "Open decisions" below.
- **Alternatives:** Keep the invoice-only scope.
- **Why:** An FDE meets a different data mess at every customer. A pipeline that
  takes a target schema and handles many input formats shows that more directly
  than a single-purpose invoice tool.
- **Consequences:** The brief in `CLAUDE.md`, the generator design, the extraction
  schema, the evals and the milestones all need revisiting once the details are
  settled. The original invoice question about corpus scale (about 400 invoices,
  150 PDFs) is withdrawn.

---

## Open decisions (waiting on the owner)

1. **Currency in ground truth.** From the Milestone 1 plan; still applies if any
   dataset has money in more than one currency. Recommendation: store amounts in
   their original currency only, and compute USD truth from cached ECB rates using
   the pipeline's own FX client.
2. **Re-scope details (D-003).** What "generic" means: a schema that can be
   swapped, a choice of demo datasets, whether the fictional customer scenario
   stays, which text formats count, and which original stages survive.
