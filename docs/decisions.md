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

## D-004: The engine is driven by the schema, with domain packs
- **Date:** 2026-10-09 · **Milestone:** 1 · **Status:** Accepted (owner)
- **Context:** "Generic" still needs a target to extract into. LLM extraction and
  the evals both need a schema and ground truth.
- **Choice:** The engine takes a domain pack as config. A pack holds the Pydantic
  target model (with field descriptions and aliases), the validation rules, the
  entity keys for matching and dedupe, and a seeded generator with ground truth.
  We ship several packs to prove the engine is generic.
- **Alternatives:** Fully schema-free, where the LLM infers the schema (most
  generic, but hard to evaluate and less reliable). A fixed set of 2–3 hard-coded
  schemas (simpler, but no plug-in point).
- **Why:** This is how the work goes with a real customer: agree on the target
  schema, then make the messy data fit it. Fixed schemas keep the evals honest.
  Adding a second pack without touching the engine is what proves it is generic.
- **Consequences:** Pack code must not leak into the engine; tests should run the
  engine against more than one pack. Supersedes the single `Invoice` model in the
  original brief.

## D-005: No customer scenario; present it as a generic tool
- **Date:** 2026-10-09 · **Milestone:** 1 · **Status:** Accepted (owner)
- **Context:** The original brief framed everything around Coastline Facilities
  Group, and the README was to read as a client case study.
- **Choice:** Drop the fictional customer. Present the system as a generic tool,
  with datasets in a benchmark style. The README becomes a project write-up: the
  problem, a scorecard, the architecture, decisions, next steps for a real
  deployment, and how to run it.
- **Alternatives:** A generic engine with 1–2 fictional customer stories for the
  demo, or keep Coastline but widen its data.
- **Why:** This is the owner's call; the framing stays general rather than tied to
  one domain.
- **Consequences:** The README results scorecard reports per pack and per format,
  not as one business question. The "What I'd do next" section speaks to a generic
  real deployment.

## D-006: Supported input formats
- **Date:** 2026-10-09 · **Milestone:** 1 · **Status:** Accepted (owner)
- **Context:** A generic messy-data system needs to cover the formats customer data
  actually comes in.
- **Choice:** CSV, Excel (.xlsx), PDF (text layer and scanned image-only), TXT/MD,
  EML, DOCX, and JSON/JSONL/log lines. The generator plants realistic mess in each
  one (the table is in `CLAUDE.md`).
- **Alternatives:** Fewer formats (less to build, but a weaker demonstration), or
  more (HTML, images other than PDF, XML).
- **Why:** Together these cover structured (CSV/Excel/JSON), semi-structured
  (EML/DOCX/logs) and unstructured (PDF/TXT) data, which is the full range of
  extraction difficulty.
- **Consequences:** New dependencies: openpyxl and python-docx (`email` is in the
  standard library). Ingest needs one adapter per format, and source locations must
  handle sheet/cell, page, line and email part.

## D-007: What carries over from the original brief
- **Date:** 2026-10-09 · **Milestone:** 1 · **Status:** Accepted (owner)
- **Context:** Some parts of the original invoice brief were specific to the
  domain.
- **Choice:** Keep reconciliation across sources (fuzzy entity matching, dedupe,
  linking records across sources), scanned/image PDFs sent to Claude as documents,
  and the review UI with its audit trail. Drop the FX conversion stage and the
  Frankfurter/ECB client.
- **Alternatives:** Keep FX as an optional step that only a pack with money fields
  would use.
- **Why:** Reconciliation and human review are the core of the project. FX was a
  feature of the invoice domain, not of the engine.
- **Consequences:** Removes httpx and the external-API story from the stack.
  Currency becomes a plain field where a pack has one. The earlier open question
  about currency in ground truth is closed: no conversion, amounts stay in their
  original currency.

## D-008: Two domain packs in Milestone 1: invoices and contacts
- **Date:** 2026-10-09 · **Milestone:** 1 · **Status:** Accepted (owner)
- **Context:** D-004 requires more than one pack to prove the engine is generic,
  and the packs together must cover every format in D-006.
- **Choice:** `invoices` (PDF text and scanned, CSV, XLSX) and `contacts` (EML,
  DOCX, TXT/MD, JSONL and log lines, CSV).
- **Alternatives:** Three packs now (support tickets or a product catalog as the
  third), or one pack covering every format.
- **Why:** The two packs stress different things. Invoices are heavy on numbers,
  layouts and scans; contacts are heavy on entity matching and free text. Two packs
  keep the milestone reviewable. A third pack added later with no engine change is
  the stronger proof that the design is generic.
- **Consequences:** Engine tests must run against both packs. A third pack is a
  candidate for after Milestone 7.

## D-009: Corpus scale of 300 true records per pack
- **Date:** 2026-10-09 · **Milestone:** 1 · **Status:** Accepted (owner)
- **Context:** The corpus has to be big enough for meaningful eval percentages and
  small enough that a full LLM extraction run stays cheap.
- **Choice:** 300 true records per pack.
  - Invoices: about 150 PDFs (about 18 of them scanned, plus planted duplicates),
    two CSV exports and one XLSX aging report.
  - Contacts: about 80 emails, 20 DOCX files, 40 TXT/MD notes, one JSONL file and
    one log file, plus a CRM CSV.
- **Alternatives:** Smaller (faster and cheaper, but noisy metrics), or larger
  (tighter metrics, but more cost per run).
- **Why:** About 300 unstructured documents to extract per full run is a sensible
  cost, and caching means repeat runs cost nothing.
- **Consequences:** Counts live in one config object, so the scale can be changed
  later without code changes.

## D-010: Generate the truth first, then spread it into messy sources
- **Date:** 2026-10-09 · **Milestone:** 1 · **Status:** Accepted
- **Context:** Reconciliation and extraction can only be scored if we know which
  source rows and documents describe the same real-world record.
- **Choice:** Each pack first generates clean true records, then writes each record
  into several sources. Every placement is recorded as an *occurrence*: file plus
  location, the field values that source states (`expected`), and the mess planted
  there (`defects`).
- **Alternatives:** Generate each source on its own and link them afterwards
  (fragile). Store only the clean truth, with no per-source expected values (then a
  source showing a stale title would wrongly count as an extraction error).
- **Why:** Extraction is scored against what the document actually says.
  Validation and reconciliation are scored against the truth. The defect labels
  let evals break results down by mess type, e.g. how well the system handles
  nicknames or serial dates.
- **Consequences:** `ground_truth.json` is the contract for the evals.
  Truth values may legitimately disagree with a source; for example, a PDF can print
  a total that doesn't add up while the truth holds the correct arithmetic.

## D-011: Byte-level determinism, checked by a hash manifest
- **Date:** 2026-10-09 · **Milestone:** 1 · **Status:** Accepted
- **Context:** "The same corpus every time" needs to hold byte for byte, otherwise
  LLM caching by content hash and eval comparisons between runs fall apart.
- **Choice:**
  - One seeded RNG per pack and stage (`sub_rng`, seeded from strings, never from
    `hash()`). Changing one pack doesn't disturb the others.
  - reportlab runs with `invariant=True`.
  - XLSX and DOCX files are zip archives. They are rewritten with fixed entry
    times, and the timestamps in their document metadata are pinned (openpyxl
    otherwise stamps the save time there).
  - Email MIME boundaries are fixed.
  - `manifest.json` stores a SHA-256 for every file. A test regenerates the corpus
    in a fresh process and compares manifests.
- **Alternatives:** Compare only the ground truth (misses drift in binary files),
  or commit the corpus (rejected in D-002).
- **Why:** The test caught a real leak while building this milestone: the XLSX
  "modified" timestamp.
- **Consequences:** Bit-identical output assumes the same library versions, which
  `uv.lock` pins. Upgrading reportlab, Pillow, pypdfium2 or libjpeg may change hashes;
  that is expected and means regenerating.

## D-012: Scans are rasterized from the text PDFs
- **Date:** 2026-10-09 · **Milestone:** 1 · **Status:** Accepted
- **Context:** We need about 18 image-only scans whose layouts match the vendors' text
  PDFs.
- **Choice:** Render the text PDF with pypdfium2, then grayscale, rotate (sometimes
  by 90° or 180°), lower the contrast, add Gaussian noise and blur, encode as JPEG,
  and wrap the image in a PDF with no text layer.
- **Alternatives:** Draw scans separately with Pillow (a second layout engine to
  maintain), or use poppler/pdf2image (a system dependency that CI would also need).
- **Why:** There is one layout engine, scans match their vendors, and pypdfium2 is a
  pip wheel with no system packages.
- **Consequences:** pypdfium2 is in the `gen` dependency group only.

## D-013: Contact details come from reserved fictional ranges
- **Date:** 2026-10-09 · **Milestone:** 1 · **Status:** Accepted
- **Context:** The working rules forbid real people or company data. Random
  emails and phone numbers can collide with real ones.
- **Choice:**
  - Emails use the reserved `.example` top-level domain (RFC 2606).
  - North American numbers use the 555-0100..0199 block reserved for fiction.
  - UK numbers use Ofcom's ranges reserved for drama.
  - Vendor and company names are hand-picked to be generic.
  - Tests assert the email and phone ranges.
- **Alternatives:** Faker, which can produce realistic-looking real domains and
  numbers.
- **Why:** Nothing generated can reach a real inbox or phone, and the guarantee is
  tested.
- **Consequences:** Phone pools are small (100 numbers per North American area
  code), which caps the corpus size per city. That is fine at 300 records per pack.

## D-014: Dependency groups, added as each milestone needs them
- **Date:** 2026-10-09 · **Milestone:** 1 · **Status:** Accepted
- **Context:** The stack lists many libraries; most aren't used until later
  milestones.
- **Choice:**
  - Runtime dependencies are only what the code imports today, plus the parsers
    the ingest layer will use (pdfplumber, pypdf, openpyxl, python-docx).
  - Generator-only libraries sit in a `gen` group, dev tools in a `dev` group.
  - Each later milestone adds its own dependencies (anthropic, SQLAlchemy,
    Streamlit, ...).
- **Alternatives:** Install the whole stack up front.
- **Why:** Installs stay small, and every dependency in the lockfile has a
  reason in the code.
- **Consequences:** `pyproject.toml` changes at most milestones.

## D-015: Secret scanning with gitleaks on commit and over full history in CI
- **Date:** 2026-10-09 · **Milestone:** 1 · **Status:** Accepted
- **Context:** The working rules require a secret-scanning pre-commit hook. Testing
  showed two gaps: gitleaks v8.21 has no Anthropic API key rule, and the upstream
  hook only scans staged changes, so in CI it would scan nothing.
- **Choice:** Use gitleaks v8.30.0, which has the `anthropic-api-key` rule, verified
  by staging a fake key. The default hook runs on commit. A manual-stage
  `gitleaks-history` alias scans the full git history, and CI runs it with
  `fetch-depth: 0`.
- **Alternatives:** detect-secrets (needs a baseline file to maintain), or the
  gitleaks GitHub Action (a separate code path from local runs).
- **Why:** One tool and one config, locally and in CI, and it is proven to catch
  the key type this project actually uses.
- **Consequences:** pre-commit builds gitleaks from source with Go the first time it
  runs; after that it is cached.

---

## Open decisions (waiting on the owner)

None right now.
