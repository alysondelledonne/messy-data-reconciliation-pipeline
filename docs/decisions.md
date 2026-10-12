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

## D-016: How big a raw record is
- **Date:** 2026-10-11 · **Milestone:** 2 · **Status:** Accepted (owner)
- **Context:** Ingest has to cut each file into units that later stages extract
  from and cite. Units that are too fine lose context; units that are too coarse
  make locations vague.
- **Choice:** It depends on the source:
  - Structured sources get one record per row or line: CSV row, XLSX row per sheet,
    JSONL line, log line, DOCX table row.
  - Unstructured sources get one record per document or part: a PDF, a TXT/MD file
    (covering all its lines), an email's own text, its quoted reply chain, each
    attachment, and each DOCX paragraph.
  - In Milestone 3, extraction returns zero or more records per raw record.
- **Alternatives:** Always split to the smallest unit (a line or paragraph), or one
  record per file everywhere.
- **Why:** A signature block or an invoice only makes sense as a whole, while a
  CSV row already is a whole record. Splitting an email into own text and quoted
  chain stops one person's details being attributed to another.
- **Consequences:** Locations can be spans (`line_start`/`line_end`). The shared
  `location_covers()` decides whether a record contains a finer location; the
  evals reuse it.

## D-017: Database tests run on real Postgres, required in CI and skipped locally
- **Date:** 2026-10-11 · **Milestone:** 2 · **Status:** Accepted (owner)
- **Context:** The store uses Postgres-only features (JSONB and its operators). Tests
  must exercise the real thing without making Docker mandatory for every test run.
- **Choice:**
  - CI runs a `postgres:16` service container and sets `REQUIRE_DB=1`, so the DB
    tests fail rather than skip.
  - Locally they run when `docker compose` is up and skip otherwise.
  - Tests use a separate `<db>_test` database, recreated and migrated once per
    session. Each test runs inside a transaction that is rolled back.
- **Alternatives:** testcontainers (needs Docker even for unit tests), or SQLite (not
  the deployed database, and its JSON behaves differently).
- **Why:** Tests run against the same engine we deploy, while unit tests stay fast
  and Docker-free.
- **Consequences:** A developer without Docker sees skipped tests, not failures.
  CI is the backstop.

## D-018: Spreadsheet structure is repaired at ingest
- **Date:** 2026-10-11 · **Milestone:** 2 · **Status:** Accepted (owner)
- **Context:** Report-style spreadsheets have title rows, grouped rows where only
  the first row names the group, subtotal and total rows, and stray notes.
- **Choice:**
  - Ingest finds the header row by itself.
  - It fills down a blank leading column within a group, flagged
    `filled_down:<column>`.
  - Total and subtotal rows (`total_row`) and stray notes (`stray_note`) are kept
    as separate kinds.
  - A sheet with no table becomes one `sheet_text` record.
- **Alternatives:** Keep rows exactly as they are and let the LLM sort it out
  (costs more, harder to test).
- **Why:** These are structural repairs, deterministic and testable against
  ground truth. Nothing is dropped, and every inferred value is flagged.
- **Consequences:** Fill-down applies only to the leftmost column of XLSX sheets.
  Other layouts may need a pack-level hint later.

## D-019: Keep the original value next to the normalized one; never guess silently
- **Date:** 2026-10-11 · **Milestone:** 2 · **Status:** Accepted
- **Context:** Normalizing dates and amounts helps every later stage, but a wrong
  guess, presented as clean data, is worse than no guess.
- **Choice:** Every record stores `raw` (the source values, decoded to text) and
  `normalized` (typed readings with flags) side by side. Conventions:
  - **Slash dates** are read month-first. If both parts are 12 or less, the date is
    flagged `ambiguous_day_month`; a first part over 12 means day-first, flagged
    `day_first`.
  - **Dot dates** are read day-first.
  - **Dates with no year** become a partial (`--04-03`) flagged `missing_year`.
  - **Excel serial numbers** are converted only in date-named columns, never in
    count columns such as "Days Past Due".
  - **Amounts** must match strict US or EU thousands grouping. A bare number counts
    as money only with a currency marker or a money-named column. `1,234` and
    `1.234` are flagged `ambiguous_separator`.
- **Alternatives:** dateutil-style lenient parsing, which silently picks an
  interpretation; or keep only the cleaned value.
- **Why:** The review queue and the evals can see exactly which values were
  inferred. Tests caught two such bugs: phone numbers read as amounts, and a day
  count read as a date.
- **Consequences:** Resolving ambiguous and partial dates, for example a missing
  year taken from a "Period" column, belongs to extraction or the pack, not ingest.

## D-020: Re-ingest is keyed on pack, path and content hash
- **Date:** 2026-10-11 · **Milestone:** 2 · **Status:** Accepted
- **Context:** Running ingest twice must be safe. But identical bytes under two names
  is a real duplicate signal, not a reason to drop a file.
- **Choice:** `source_files` is unique on `(pack, path, sha256)`.
  - Same path and same content: skipped.
  - Same path, new content: stored as a new version.
  - Same content under another path: stored, with `meta.identical_to` listing the
    twin paths.
- **Alternatives:** Unique on the content hash alone (would silently drop the
  planted exact-copy PDFs), or on path alone (loses file versions).
- **Why:** Idempotent runs, with exact duplicates left visible for reconciliation.
- **Consequences:** Milestone 5 can treat `identical_to` as a strong duplicate
  signal.

## D-021: Detect encodings without adding a dependency
- **Date:** 2026-10-11 · **Milestone:** 2 · **Status:** Accepted
- **Context:** Inputs mix UTF-8, UTF-8 with a BOM, and Latin-1. Email attachments
  sometimes declare no charset.
- **Choice:** Try UTF-8 with a BOM, then strict UTF-8, then cp1252, then Latin-1
  (which never fails), and record the result. Text attachments are decoded from
  their bytes the same way, instead of trusting the MIME charset.
- **Alternatives:** charset-normalizer or chardet (statistical guessing, plus a
  dependency).
- **Why:** UTF-8 validation is strict enough to tell UTF-8 from the legacy
  encodings reliably, and the order is easy to reason about. A test caught the
  attachment case: a vCard with no charset had its "ü" mangled.
- **Consequences:** Non-Western legacy encodings, such as Shift-JIS, would come out
  as cp1252 text. Add a detector if a real dataset needs one.

## D-022: Hand-written migrations, checked against the models
- **Date:** 2026-10-11 · **Milestone:** 2 · **Status:** Accepted
- **Context:** Migrations and models can drift apart.
- **Choice:** Alembic migrations live in `migrations/versions/`, written by hand
  (autogenerate is allowed as a starting point). A test runs Alembic's
  `compare_metadata` against a freshly migrated database and fails on any
  difference. Another test downgrades to base and upgrades again.
- **Alternatives:** `Base.metadata.create_all()` without migrations, or trusting
  autogenerate.
- **Why:** Schema changes are reviewable and reversible, and drift fails CI.
- **Consequences:** Every model change needs a new revision in the same commit.

## D-023: Structured sources go through a column mapping proposed once per header layout
- **Date:** 2026-10-11 · **Milestone:** 3 · **Status:** Accepted (owner)
- **Context:** CSV, XLSX, DOCX table rows and JSON objects are already split into
  columns at ingest, with typed values next to the originals. Sending every row to
  the model would cost about 90× more calls for little gain.
- **Choice:**
  - For each distinct header layout (column list, or flattened JSON key set), the
    model sees the columns, up to 8 sample rows, the distinct values of
    low-cardinality columns, and file context such as report titles and sheet
    names. It proposes, per target field, a column, a transform (`copy`, `date`,
    `amount`, `split_name_first/last`, `phone_e164`, `country_iso2`), a value map
    for enum fields, or a file-level constant.
  - The mapping is cached in `column_mappings`.
  - Rows are then read deterministically: values come from ingest's normalized
    cells, confidence is the mapping's confidence reduced for flags such as
    `ambiguous_day_month` or `filled_down`, and evidence is `Column: cell`.
- **Alternatives:** One model call per row, which copes better with odd cells but
  costs far more.
- **Why:** About 10 mapping calls instead of about 900 row calls. The result is
  deterministic and easy to explain, and inferred values stay flagged. Testing
  showed the need for file-level constants: an aging report's title ("Open Items")
  is the only place that says every row is unpaid.
- **Consequences:** Cells that combine several fields in one column aren't split
  beyond names. A row whose date has no year comes out with a null date flagged
  `missing_year`; using a "Period" column to fill the year is left to later stages.

## D-024: Structured outputs, not a forced tool call
- **Date:** 2026-10-11 · **Milestone:** 3 · **Status:** Accepted (owner); supersedes the
  brief's "schema as a tool"
- **Context:** The brief said to pass the Pydantic schema as a tool. Forcing a tool
  call (`tool_choice` any/tool) returns a 400 on Claude Opus 5.5 and Sonnet 5.5, so it
  would break on escalation.
- **Choice:** Send the schema as `output_config.format` (`json_schema`), which works
  on every model we use. The schema is still generated from the pack's Pydantic
  model. Every field is wrapped as `{value, confidence, evidence}` inside
  `{records: [...]}`. Decimals travel as strings and enums as `enum`; every object
  has `additionalProperties: false` and every property is required but nullable.
  Values are checked field by field against the pack schema, and an invalid value
  becomes null, flagged `invalid_value`.
- **Alternatives:** A strict tool with `tool_choice: auto`, plus retrying when the
  model doesn't call it.
- **Why:** The same schema-driven contract with fewer moving parts and a guaranteed
  JSON shape.
- **Consequences:** Citations can't be combined with structured outputs, so evidence
  comes from the model's own quotes, checked by D-028.

## D-025: Claude Haiku 5.5 by default, escalating to Claude Opus 5.5
- **Date:** 2026-10-11 · **Milestone:** 3 · **Status:** Accepted (owner)
- **Context:** The brief asks for a smaller model by default and escalation for hard
  documents.
- **Choice:**
  - `EXTRACT_MODEL=claude-haiku-5-5` and `ESCALATION_MODEL=claude-opus-5-5`, both
    at effort `medium`. All of this is configurable.
  - Every call goes to the default model first. Broken output (invalid JSON,
    `max_tokens`, API error) gets one retry. A request escalates on a refusal (Haiku
    has no server-side fallback), on output still broken after the retry, on a
    present required value below `CONFIDENCE_THRESHOLD` (0.6), or when more than a
    third of values fail the evidence check.
  - A missing required value is **not** an escalation trigger. It means the model
    is saying "not stated", so it's flagged `missing_required:<field>` for the
    validation stage. (A compact invoice layout really never prints its currency.)
  - Escalation calls opt into server-side refusal fallbacks (`fallbacks: "default"`).
- **Alternatives:** Sonnet 5.5 as the escalation model (half the price), or one
  model for everything.
- **Why:** Most documents are routine. Spending Opus prices only on the hard ones
  keeps a full run cheap: the estimate is $0.25–$0.85 on Haiku plus escalations.
  Real costs will come from the first live run.
- **Consequences:** The escalation rate and threshold need tuning against the evals
  in Milestone 7. The cost per model is visible in `llm_calls`.

## D-026: Model responses are cached and costed in Postgres (`llm_calls`)
- **Date:** 2026-10-11 · **Milestone:** 3 · **Status:** Accepted (owner)
- **Context:** Re-runs must not pay twice, and the UI and evals need token and cost
  data.
- **Choice:**
  - Every call is logged with requested and served model, status, stop reason,
    input, output and cache tokens, USD cost (from configurable list prices),
    latency and the parsed response.
  - Successful calls carry a cache key: a SHA-256 of the prompt version, model,
    effort, system prompt, schema and content. A matching key is answered from the
    table.
  - Failed calls get a null key, so a retry really calls the model again.
  - The system prompt also carries `cache_control` for API-side prompt caching.
- **Alternatives:** A JSON file cache in `.cache/`.
- **Why:** One store that can be queried for cost per pack and model and for cache
  hit rate, and that the Overview page and the evals can read.
- **Consequences:** Bumping `PROMPT_VERSION` deliberately invalidates the cache. The
  system prompt is likely below the minimum size for API prompt caching, so the main
  saving comes from our own cache.

## D-027: Calls are concurrent, and CI uses a fake LLM built from ground truth
- **Date:** 2026-10-11 · **Milestone:** 3 · **Status:** Accepted
- **Context:** About 450 sequential calls would take a long time. Tests must run
  without a key.
- **Choice:**
  - `LLMClient` is a small interface. The real implementation calls the Anthropic
    SDK, which retries 429s, 5xx and connection errors itself. Calls run on
    `EXTRACT_WORKERS` (8) threads.
  - Tests use `tests/fakes.py`, which answers document requests with the
    ground-truth `expected` values (quoting their printed form as evidence) and
    mapping requests with a column-name rulebook. Tests can also script refusals,
    broken output and low confidence.
  - tenacity is dropped from the stack, because the SDK's retries cover transport
    errors.
- **Alternatives:** Recorded API responses (cassettes): more realistic, but they need
  a key to record and go stale whenever the prompt changes.
- **Why:** The whole stage, including caching, retries, escalation and storage, runs
  end to end in CI on the real corpus.
- **Consequences:** The fake proves plumbing and mapping logic, not model quality.
  Accuracy numbers come only from live runs (Milestone 7).
  - Building the fake found three bugs in the generator's ground truth, all fixed in
    generator 1.1.0: a currency claimed for an invoice layout that never prints
    one, and phone and country fields whose text and `expected` were decided by
    separate random draws.

## D-028: Every value's evidence is checked against the source text
- **Date:** 2026-10-11 · **Milestone:** 3 · **Status:** Accepted
- **Context:** A model can return confident values that aren't in the document.
- **Choice:** After parsing, each value's evidence quote (or, failing that, the value
  itself, or its digits when there are 6 or more) must appear in the source text
  after normalizing case and whitespace.
  - If it doesn't, confidence is halved and the value is flagged
    `grounding_failed`.
  - Scans have no text to check against, so they get `grounded: null`.
  - Mapped values count as grounded, because they come straight from cells.
- **Alternatives:** Trust the model's confidence alone, or use citations (which can't
  be combined with structured outputs).
- **Why:** It costs nothing and catches made-up values, and its rate is an
  escalation trigger (D-025).
- **Consequences:** A model that reformats without quoting the printed form gets
  penalized. The prompt asks for exact quotes for that reason.

## D-029: Three statuses decided by finding severity
- **Date:** 2026-10-12 · **Milestone:** 4 · **Status:** Accepted (owner)
- **Context:** Every extracted record needs a status (`auto_approved`,
  `needs_review` or `rejected`) with reasons a person can act on.
- **Choice:**
  - Each check produces findings with a severity: `reject`, `review` or `info`.
  - Any `reject` finding means `rejected`; any `review` finding means
    `needs_review`; otherwise the record is `auto_approved`.
  - `reject` is reserved for records with nothing to identify them by: an invoice
    with no vendor and no number, or no amount; a contact with no name, email or
    phone.
  - Each reason is a full sentence naming its fields, for example "Subtotal 250.00 +
    tax 20.00 = 270.00, but the total says 300.00 (off by 30.00)."
- **Alternatives:** A weighted score with cut-offs (harder to explain), or rejecting
  every rule failure (loses records that a human could fix in seconds).
- **Why:** The review queue can show exactly why each record is there, and
  automatic rejection stays rare and obviously correct.
- **Consequences:** These statuses are per source and come before reconciliation.
  Many reviews are gaps that a second source can fill, such as a currency missing
  from a legacy CSV row but printed on the PDF of the same invoice. Milestone 5
  should re-validate merged records.

## D-030: Rules live in the domain pack as pure functions
- **Date:** 2026-10-12 · **Milestone:** 4 · **Status:** Accepted (owner)
- **Context:** D-004: everything specific to a domain belongs to its pack, and the
  engine stays generic.
- **Choice:**
  - Each pack has a `rules.py` listing `RULES`: functions from a `RecordView`
    (values, per-field metadata, how the record was extracted) to a list of
    findings. It also has a `RULES_VERSION`.
  - Invoices: identifiable; line items sum to the subtotal; quantity × price = line
    amount; subtotal (or the line items, when no subtotal is printed) + tax =
    total; positive total and a 0–30% tax rate; plausible invoice and due dates.
  - Contacts: identifiable; email format; E.164 phone; ISO country code; name shape
    (abbreviated, odd characters; all caps is info only).
  - The engine adds generic checks on the extraction itself: missing required
    values, values that couldn't be read, dates with no year, low confidence,
    evidence not found in the source, and a failed escalation.
- **Alternatives:** A rules DSL or YAML config, or a rules engine library.
- **Why:** Plain functions are trivial to test, read and extend, and a new pack
  brings its own rules without touching the engine.
- **Consequences:** Bump `RULES_VERSION` when a rule changes meaning.

## D-031: Review threshold 0.75, above the escalation threshold 0.6
- **Date:** 2026-10-12 · **Milestone:** 4 · **Status:** Accepted (owner)
- **Context:** Extraction already escalates below 0.6 (D-025). Validation needs its
  own bar for "trust without looking".
- **Choice:** `REVIEW_CONFIDENCE=0.75`. A present value whose final confidence is
  below it goes to review.
  - Values between 0.6 and 0.75 are not worth paying the bigger model for, but they
    do deserve a human look.
  - Mapping penalties (D-023) land on the right side of the line: an ambiguous
    day/month date (about 0.63–0.67) or a UK phone that can't be made E.164 (0.57)
    goes to review. A filled-down supplier (0.86) or a currency inferred from "$"
    (0.80) is approved.
- **Alternatives:** One threshold for both (then everything under the bar either
  costs Opus money or bypasses review), or 0.85 (it sent every inferred currency to
  review).
- **Why:** It separates "the model isn't sure" (escalate) from "the source is
  ambiguous" (ask a human).
- **Consequences:** The threshold is part of the validator version. Tune it against
  review-queue precision in Milestone 7.

## D-032: Verdicts stored per validator version and recomputed each run
- **Date:** 2026-10-12 · **Milestone:** 4 · **Status:** Accepted (owner)
- **Context:** Validation is cheap and deterministic. Human decisions (Milestone 6)
  must never be lost to a re-run.
- **Choice:**
  - The `validations` table holds one row per extracted record and validator
    version: the rules version plus a hash of the thresholds. It stores the status
    and the findings as JSON.
  - `validate` deletes and rewrites the rows for the current version. Other
    versions are kept, so threshold experiments can be compared side by side.
  - Human approvals and corrections will live in their own tables with an audit
    trail.
- **Alternatives:** A status column on `extracted_records` (one version only,
  overwritten by every run), or incremental validation (more code for no gain at
  this size).
- **Why:** It's idempotent and simple, and machine verdicts and human decisions
  never overwrite each other.
- **Consequences:** The UI and evals read the current version's verdicts.
  - Building this found two more ground-truth bugs in the generator, fixed in
    generator 1.2.0: email signatures that didn't print the city still claimed
    one. And the subtotal-less "statement" layout needed the totals rule to fall
    back to the line items, or one planted bad total was missed.

## D-033: Entity matching: normalized names, a lookalike guard, evidence, thresholds 92/80
- **Date:** 2026-10-12 · **Milestone:** 5 · **Status:** Accepted (owner)
- **Context:** Vendor and company names come with typos, abbreviations, missing legal
  suffixes and cut-off text. The packs also plant genuinely different organizations
  with confusingly similar names (D-004). Name similarity alone scores "Bluewater
  Holdings" vs "Bluewater Holding Group" at 97.
- **Choice:**
  - **Normalize** (`reconcile/names.py`): fold accents and case, `&` becomes "and",
    expand common abbreviations (Svcs, Mech, Contr…), drop trailing legal suffixes
    and cut-off single letters.
  - **Score** with rapidfuzz: the better of `token_sort_ratio` and `ratio`.
  - **Lookalike guard:** words in one name with no counterpart in the other
    (typos, truncations and abbreviations count as counterparts). "Elevator" vs
    "Electrical" or "Partners" vs "Advisors" is guarded.
  - **Evidence** from the pack: identifiers that prove two spellings are one
    organization when shared, and identifier families that suggest different
    organizations when they don't overlap. Invoices use invoice numbers and their
    letter prefix; contacts use email domains.
  - **Decision:**
    - merge at ≥ 92 with no guard;
    - merge from 70 with shared evidence and no guard;
    - send 80–92, guarded pairs and pairs with disjoint evidence to adjudication
      (D-036);
    - keep separate below 80.
  - **Clustering:** merges are applied best score first. A merge is refused if it
    would put two names judged separate or still undecided into one cluster, so an
    alias can't bridge two lookalikes.
  - **Canonical name:** well-formed (mixed case), of the most-mentioned name (not a
    typo of it), without abbreviations, with its legal suffix, seen on the
    organization's own documents, then the most common.
- **Alternatives:** Similarity only, at one threshold (lookalikes merge or typos
  split, depending on the threshold). Embeddings (a dependency and cost for names
  that rapidfuzz handles). The LLM on every pair (a few hundred calls for what rules
  settle).
- **Why:** Rules settle the easy 95%. Evidence and the guard catch the dangerous
  pairs before they reach a threshold.
- **Consequences:** Thresholds are settings (`MATCH_AUTO`, `MATCH_REVIEW`). On the
  seed-42 corpus with the fake model, entity pairs are 100% right in both packs.
  Milestone 7 measures this properly.

## D-034: Golden records, record links and entities as tables
- **Date:** 2026-10-12 · **Milestone:** 5 · **Status:** Accepted (owner)
- **Context:** The brief asks for a deduplicated table where every value is traced
  to its source, with conflicts reported field by field.
- **Choice:** Migration 0004 adds:
  - `entities`: canonical name plus every alias with its count;
  - `golden_records`: the chosen fields, `provenance` per field (rule, winning
    record, supporting records, alternatives, spelling variants), `conflicts`, a
    status and findings, and the number of sources;
  - `record_links`: golden record → extracted record, with the reason it joined
    (seed, invoice number, email, phone, soft match, name only);
  - `match_decisions`: one row per adjudicated name pair (D-036).
  Reconciliation deletes and rebuilds a pack's golden records and entities on every
  run; match decisions are kept.
- **Linking** (`link_records`):
  1. equal strong keys join (invoice number or PO within a vendor; email; E.164
     phone), if the records are compatible;
  2. within a soft-key block (same vendor and total; same company and name), records
     join if they match and are compatible with every member of the other cluster;
  3. a record too sparse for a soft key (no company named) joins the single cluster
     that shares its name.
  A record that fits two clusters which can't be one record (namesakes, two invoice
  numbers) stays alone and gets an `ambiguous_match` review finding.
  Compatibility is per pack: different invoice numbers are two invoices; different
  email addresses (not a one-key typo) or names that disagree are two people.
  Phone numbers prove nothing either way: old sources carry old numbers, and the
  corpus has a number that passed from one person to another.
- **Alternatives:** Columns on `extracted_records` (no place for conflicts or
  provenance). Incremental reconciliation (more code; a full rebuild takes 3 s per
  pack).
- **Why:** Rebuilding is idempotent and simple, and provenance makes every value
  explainable in the review UI.
- **Consequences:** Human corrections to golden records (Milestone 6) must be stored
  apart from them, keyed so that they survive a rebuild.

## D-035: Survivorship policies declared per pack
- **Date:** 2026-10-12 · **Milestone:** 5 · **Status:** Accepted (owner)
- **Context:** When linked sources disagree, the right winner depends on the field.
- **Choice:** Each pack's `ReconcileSpec` names a policy per field:
  - `amount` (invoice totals, subtotal, tax): most sources, then arithmetically
    consistent sources, then trust;
  - `system_of_record` (payment status): the AP register, configured by a filename
    pattern;
  - `recent` (email, phone, job title, company): newest source;
  - `majority` (identity fields), `richest` (line items), `entity` (canonical name).
  Before a policy picks, values that mean the same thing are grouped as variants:
  `LSC-3177` = `LSC3177`, `PO PO-2489640` (a register memo copied whole) =
  `PO-2489640`, `0117 4960 320` = `+441174960320`, Mike = Michael, an email one
  keystroke off. Variants aren't conflicts, and the pack chooses which
  form to show (`display_rank`).
  - A national phone number gets the record's country code after survivorship
    (`finalize`).
  - **Dates for "recent":** a record's own date (row date, epoch timestamp, a quoted
    email's header line), else a file date (email `Date:`, a document's `Date:` or
    "Last updated" line, a note's first line, a date in the file name). An undated
    CRM export is a current snapshot; other undated sources count as oldest.
  - Conflicts on **material** fields (amounts, payment status, invoice date,
    currency; contacts' last name) add a `source_conflict` review finding naming the
    winner, the rule and the losing sources.
  - The golden record is **re-validated** with the pack rules. Cluster checks add
    `not_in_ap_register` (review), `no_document` (info), `duplicate_document`
    (review) and `duplicate_row` (info). Source-level verdicts are kept.
- **Alternatives:** One global rule (most recent, or most trusted format). Always
  asking a human (the queue fills with conflicts the rules settle).
- **Why:** Policies are domain knowledge and belong in the pack (D-004). The engine
  stays generic.
- **Consequences:** Generator 1.3.0 makes contact staleness depend on dates: each
  person who changed jobs has a change date, and only sources dated before it show
  the old title, phone or employer. Before this, a stale value could appear in a
  source newer than the current one, and recency couldn't work. Changing contacts
  files means re-extracting them (cached calls for unchanged files are reused).

## D-036: Close calls go to Claude Haiku, then to a human; decisions are stored
- **Date:** 2026-10-12 · **Milestone:** 5 · **Status:** Accepted (owner)
- **Context:** Some name pairs can't be settled by rules (D-033). A wrong merge
  corrupts records; a wrong split only adds review work.
- **Choice:**
  - Gray pairs are sent to the extraction model with structured outputs:
    `{same, confidence, reason}`. The request includes the spellings, mention
    counts, identifiers and identifier families, the similarity score and the
    guarded words. It goes through the same cache and cost log (`llm_calls`,
    purpose `entity_match`).
  - Below `MATCH_LLM_CONFIDENCE` (0.8) the pair stays `pending`. Its golden records
    get an `entity_match_pending` review finding and the names are kept separate.
  - Every outcome is stored in `match_decisions` (`decided_by` = `llm:<model>`,
    `system` or `human:<who>`). The next run reuses a stored decision instead of
    asking again. A human decision is never overwritten.
  - `reconcile --no-llm`, `MATCH_LLM=false`, or no API key: every gray pair waits
    for a human.
- **Alternatives:** Humans only (slow for the 2–12 pairs per pack seen here). The
  model only, with no human route (no way to correct it).
- **Why:** It's cheap (a fraction of a cent per run) and explainable, and people
  stay in charge.
- **Consequences:** The review UI (Milestone 6) lists pending pairs and writes
  `human:` decisions.
  - The owner confirmed 0.8 after the first live run (2026-10-12): 1 invoice pair and
    6 contact pairs came back below it and wait for a human. A wrong merge costs more
    than a review.

## D-037: Failed extraction results are retried on the next run
- **Date:** 2026-10-12 · **Milestone:** 5 · **Status:** Accepted
- **Context:** The owner's first live run hit an API error on the column-mapping calls.
  The rows were stored with status `failed`, and `extract` treated any stored result as
  done. Every later run skipped all 372 invoice rows (AP register, procurement export,
  aging report), so every invoice looked unrecorded to reconciliation.
- **Choice:** A raw record is pending until it has a result that isn't `failed`. When a
  retry succeeds, it replaces the failed result for that raw record and extractor
  version.
- **Alternatives:** A `--retry-failed` flag (an easy step to forget, as here). Deleting
  failed rows by hand (the howto already promised that re-running retries).
- **Why:** Failed calls are never cached (D-026), so retrying costs only the calls that
  failed, and a transient error can't leave data silently missing.
- **Consequences:** A document the model refuses every time is re-sent on every run.
  That's a few cents at most; `estimate` counts these documents as pending.

---

## Open decisions (waiting on the owner)

None right now. Milestone 6 (Streamlit UI) will need decisions on:
- how a reviewer's correction to a golden record is stored so that it survives a
  rebuild (D-034);
- whether approving a golden record also approves its source records;
- who may decide pending entity matches (D-036), and whether that needs a login.
