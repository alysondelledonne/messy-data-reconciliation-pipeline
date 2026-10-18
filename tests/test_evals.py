"""The eval harness, end to end with the fake model (D-043): the full pipeline on the
seed-42 corpus must not score below `evals/baseline_fake.json`. A drop means a change
made matching, value choice or the review queue worse.

To accept a deliberate change of scores, re-run with UPDATE_EVAL_BASELINE=1 and commit
the new baseline with the reason in the message."""

import json
import os
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from data_gen.ground_truth import GroundTruth
from evals.harness import evaluate
from evals.run import history, render, save
from pipeline.ingest.models import IngestedFile
from pipeline.reconcile.engine import reconcile_pack
from tests.test_reconcile_pipeline import SETTINGS, _prepare

BASELINE = Path(__file__).resolve().parents[1] / "evals" / "baseline_fake.json"

# Higher is better for these, lower for missed errors; small tolerances absorb nothing
# but rounding, since the fake model is deterministic.
HIGHER = {
    "golden_accuracy": ("golden", "field_accuracy", "overall", "accuracy"),
    "extraction_accuracy": ("extraction", "overall", "accuracy"),
    "review_precision": ("golden", "review_queue", "precision_wrong_or_issue"),
    "purity": ("golden", "dedupe", "purity"),
    "completeness": ("golden", "dedupe", "completeness"),
    "entity_precision": ("golden", "entities", "precision"),
    "entity_recall": ("golden", "entities", "recall"),
}
LOWER = {"missed_errors": ("golden", "missed_errors", "count")}


def _get(result: dict[str, Any], path: tuple[str, ...]) -> Any:
    for key in path:
        result = result[key]
    return result


def _metrics(result: dict[str, Any]) -> dict[str, Any]:
    return {name: _get(result, path) for name, path in {**HIGHER, **LOWER}.items()}


def test_fake_eval_holds_the_baseline(
    db_session: Session,
    ingested: dict[str, IngestedFile],
    invoices_gt: GroundTruth,
    contacts_gt: GroundTruth,
    corpus_dir: Path,
    tmp_path: Path,
) -> None:
    gts = {"invoices": invoices_gt, "contacts": contacts_gt}
    current: dict[str, dict[str, Any]] = {}
    for pack, gt in gts.items():
        fake = _prepare(db_session, ingested, gts, pack, corpus_dir)
        reconcile_pack(db_session, pack, SETTINGS, fake)
        result = evaluate(db_session, gt, SETTINGS, "fake")
        current[pack] = _metrics(result)
        # the report renders and saves (to a scratch folder, not evals/results)
        assert "## Scorecard" in render(result)
        path = save(result, tmp_path)
        assert path.exists() and path.with_suffix(".md").exists()
        assert pack in history(tmp_path)

    if os.environ.get("UPDATE_EVAL_BASELINE"):
        BASELINE.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n")
    baseline = json.loads(BASELINE.read_text())
    drops = []
    for pack, metrics in baseline.items():
        for name, want in metrics.items():
            got = current[pack][name]
            if name in LOWER and got > want:
                drops.append(f"{pack}.{name}: {got} (baseline {want})")
            if name in HIGHER and want is not None and (got is None or got < want - 0.05):
                drops.append(f"{pack}.{name}: {got} (baseline {want})")
    assert not drops, "eval scores fell below the baseline:\n" + "\n".join(drops)


def test_scorecard_counts_only_live_runs(tmp_path: Path) -> None:
    from evals import scorecard

    def run(label: str, at: str, accuracy: float) -> dict[str, Any]:
        fmt = {"pdf": {"accuracy": 99.0}, "pdf_scanned": {"accuracy": 95.0}}
        return {
            "meta": {"label": label, "pack": "invoices", "seed": 42, "at": at},
            "extraction": {"overall": {"accuracy": 98.0}, "by_format": fmt},
            "golden": {
                "field_accuracy": {"overall": {"accuracy": accuracy}},
                "review_queue": {"precision_wrong_or_issue": 80.0},
                "missed_errors": {"count": 3, "of_auto_approved": 200},
                "dedupe": {"purity": 100.0, "completeness": 99.0},
                "entities": {"precision": 100.0, "recall": 100.0},
            },
            "cost": {"cost_per_100_docs": 0.31},
        }

    for name, r in {
        "a_fake": run("fake", "1", 100.0),
        "b_live": run("live-seed42", "2", 97.5),
        "c_live": run("live-seed42", "3", 98.5),
    }.items():
        (tmp_path / f"{name}.json").write_text(json.dumps(r))
    runs = scorecard.latest_live(tmp_path)
    assert len(runs) == 1 and runs[0]["golden"]["field_accuracy"]["overall"]["accuracy"] == 98.5
    block = scorecard.table(runs)
    assert "98.5%" in block and "95.0%" in block and "100.0%" not in block.split("\n")[2][:30]
    readme = f"intro\n{scorecard.START}\nold\n{scorecard.END}\nrest"
    assert (
        scorecard.updated(readme, block)
        == f"intro\n{scorecard.START}\n{block}\n{scorecard.END}\nrest"
    )
    assert "No live eval run" in scorecard.table([])
