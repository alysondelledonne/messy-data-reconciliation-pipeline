"""Corpus-wide properties: determinism, CLI behaviour, ground-truth integrity."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from data_gen.build import MANIFEST, build, write
from data_gen.corpus import sha256
from data_gen.ground_truth import GroundTruth
from tests.conftest import SEED, load_gt

PACKS = ("invoices", "contacts")


@pytest.mark.slow
def test_same_seed_reproduces_identical_corpus_in_a_fresh_process(
    corpus: dict[str, bytes], tmp_path: Path
) -> None:
    """The one command regenerates byte-identical files (no hash() or clock leakage)."""
    out = tmp_path / "gen"
    subprocess.run(
        [sys.executable, "-m", "data_gen", "--seed", str(SEED), "--out", str(out)],
        check=True,
        capture_output=True,
    )
    assert (out / MANIFEST).read_bytes() == corpus[MANIFEST]


def test_different_seed_changes_the_corpus(corpus: dict[str, bytes]) -> None:
    other = build(SEED + 1, ["contacts"])
    ours = json.loads(corpus[MANIFEST])["files"]
    theirs = json.loads(other[MANIFEST])["files"]
    common = set(ours) & set(theirs)
    assert any(ours[p] != theirs[p] for p in common)


def test_manifest_hashes_every_file(corpus: dict[str, bytes]) -> None:
    manifest = json.loads(corpus[MANIFEST])
    assert manifest["seed"] == SEED
    assert set(manifest["files"]) == set(corpus) - {MANIFEST}
    for path, meta in manifest["files"].items():
        assert meta["sha256"] == sha256(corpus[path])


def test_write_refuses_to_wipe_foreign_directory(tmp_path: Path) -> None:
    (tmp_path / "precious.txt").write_text("keep me")
    with pytest.raises(SystemExit, match="refusing"):
        write({"x.txt": b"x"}, tmp_path)
    assert (tmp_path / "precious.txt").exists()


def test_write_replaces_previous_corpus(tmp_path: Path) -> None:
    out = tmp_path / "gen"
    write({MANIFEST: b"{}", "old.txt": b"old"}, out)
    write({MANIFEST: b"{}", "new.txt": b"new"}, out)
    assert sorted(p.name for p in out.iterdir()) == [MANIFEST, "new.txt"]


@pytest.mark.parametrize("pack", PACKS)
def test_ground_truth_integrity(corpus: dict[str, bytes], pack: str) -> None:
    gt: GroundTruth = load_gt(corpus, pack)
    assert gt.pack == pack
    assert gt.seed == SEED
    assert len(gt.records) == 300
    ids = [o.occurrence_id for r in gt.records for o in r.occurrences]
    assert len(ids) == len(set(ids)), "occurrence ids must be unique"
    entity_ids = {e.entity_id for e in gt.entities}
    by_id = {o.occurrence_id: o for r in gt.records for o in r.occurrences}
    for r in gt.records:
        assert r.occurrences, f"{r.record_id} appears in no source"
        assert r.entity_id in entity_ids
        for o in r.occurrences:
            assert o.source in corpus, f"missing file {o.source}"
            if o.duplicate_of:
                assert o.duplicate_of in by_id
                assert "duplicate" in o.defects
    for group in gt.duplicate_groups:
        assert len(group) >= 2
        assert all(oid in by_id for oid in group)
        assert all(by_id[oid].duplicate_of == group[0] for oid in group[1:])
    for e in gt.entities:
        if e.lookalike_of:
            assert e.lookalike_of in entity_ids
        assert e.canonical_name not in e.aliases


@pytest.mark.parametrize("pack", PACKS)
def test_each_pack_covers_its_formats(corpus: dict[str, bytes], pack: str) -> None:
    gt = load_gt(corpus, pack)
    formats = {o.format for r in gt.records for o in r.occurrences}
    wanted = {
        "invoices": {"pdf", "pdf_scanned", "csv", "xlsx"},
        "contacts": {"eml", "docx", "txt", "md", "jsonl", "log", "csv"},
    }[pack]
    assert formats == wanted


def test_records_appear_in_multiple_sources(invoices_gt: GroundTruth) -> None:
    """Reconciliation needs overlap: most records appear in two or more files."""
    multi = sum(1 for r in invoices_gt.records if len({o.source for o in r.occurrences}) > 1)
    single = len(invoices_gt.records) - multi
    assert multi > 200
    assert single > 0, "some records should be unmatched (only in one source)"
