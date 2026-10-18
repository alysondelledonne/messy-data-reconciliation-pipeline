"""Write the README scorecard from saved eval runs (D-044). Only runs whose label starts
with "live" count, so no fake or hand-typed number reaches the README.

    uv run python -m evals.scorecard          # update README.md in place
    uv run python -m evals.scorecard --check  # exit 1 if README.md is out of date
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "evals" / "results"
README = ROOT / "README.md"
START, END = "<!-- scorecard:start -->", "<!-- scorecard:end -->"


def _pct(v: Any) -> str:
    return "n/a" if v is None else f"{v:.1f}%"


def latest_live(results: Path = RESULTS) -> list[dict[str, Any]]:
    """The newest live run per (pack, seed)."""
    best: dict[tuple[str, int], dict[str, Any]] = {}
    for p in sorted(results.glob("*.json")):
        r = json.loads(p.read_text())
        m = r["meta"]
        if not str(m["label"]).startswith("live"):
            continue
        r["_file"] = p.with_suffix(".md").name
        best[(m["pack"], m["seed"])] = r  # sorted by name = by time
    return [best[k] for k in sorted(best)]


def table(runs: list[dict[str, Any]]) -> str:
    if not runs:
        return (
            "_No live eval run recorded yet. Run the pipeline with a real API key, then "
            "`uv run python -m evals.run --pack invoices --pack contacts --label live-seed42` "
            "and `uv run python -m evals.scorecard`._"
        )
    head = (
        "| Pack (seed) | Golden field accuracy | Extraction accuracy | Text PDF / scanned PDF "
        "| Review precision | Missed errors | Dedupe purity / completeness "
        "| Entity match P / R | Cost per 100 docs | Details |\n"
        "|---|---|---|---|---|---|---|---|---|---|"
    )
    rows = []
    for r in runs:
        m, ex, g, c = r["meta"], r["extraction"], r["golden"], r["cost"]
        fmt = ex["by_format"]
        pdfs = (
            f"{_pct(fmt['pdf']['accuracy'])} / {_pct(fmt['pdf_scanned']['accuracy'])}"
            if "pdf" in fmt and "pdf_scanned" in fmt
            else "—"
        )
        me, dd, en = g["missed_errors"], g["dedupe"], g["entities"]
        rows.append(
            f"| {m['pack']} ({m['seed']}) | {_pct(g['field_accuracy']['overall']['accuracy'])} "
            f"| {_pct(ex['overall']['accuracy'])} | {pdfs} "
            f"| {_pct(g['review_queue']['precision_wrong_or_issue'])} "
            f"| {me['count']} of {me['of_auto_approved']} auto-approved "
            f"| {_pct(dd['purity'])} / {_pct(dd['completeness'])} "
            f"| {_pct(en['precision'])} / {_pct(en['recall'])} "
            f"| ${c['cost_per_100_docs']:.2f} | [run](evals/results/{r['_file']}) |"
        )
    return head + "\n" + "\n".join(rows)


def updated(readme: str, block: str) -> str:
    before, rest = readme.split(START, 1)
    _, after = rest.split(END, 1)
    return f"{before}{START}\n{block}\n{END}{after}"


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    text = README.read_text()
    new = updated(text, table(latest_live()))
    if args.check:
        sys.exit(0 if new == text else 1)
    README.write_text(new)
    print("README scorecard updated" if new != text else "README scorecard already current")


if __name__ == "__main__":
    main()
