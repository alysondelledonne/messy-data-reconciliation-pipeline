# How-to

Step-by-step commands for setting up, running, testing and operating the system.
Each section says which milestone it was added in. Only document commands that
actually work in the repo today. Planned steps are listed at the bottom and marked
as not yet available.

## Start a new working session (Claude Code)

1. Read `CLAUDE.md` (the brief and working rules) and the latest entries in
   `docs/decisions.md`.
2. Check the status column in `docs/architecture.md` to see what is built.
3. Plan the current milestone, get the owner's approval, then build.
4. Before committing, update all three docs, run the tests and propose a commit
   message.

## Record a design decision

1. Copy the template at the top of `docs/decisions.md`.
2. Give it the next `D-NNN` number and fill in context, choice, alternatives, why and
   consequences.
3. If it reverses an earlier decision, mark the old entry
   `Superseded by D-NNN` instead of deleting it.

---

## Planned (not yet available)

These sections will be filled in as each milestone lands:

- **Set up locally** (M1): install uv, `uv sync`, `pre-commit install`, copy
  `.env.example` to `.env`.
- **Generate the messy corpus** (M1): one seeded command that writes to
  `data/generated/`.
- **Run checks** (M1): ruff, mypy, pytest.
- **Start Postgres** (M1): `docker compose up -d`.
- **Ingest, extract, validate, reconcile** (M2–M5): CLI commands from
  `src/pipeline/run.py`.
- **Run the app** (M6): Streamlit.
- **Run evals** (M7): the harness and where its results are saved.
