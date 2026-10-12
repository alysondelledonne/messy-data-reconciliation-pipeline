"""Human review of golden records and name matches (D-038..D-040).

Golden records are rebuilt by every reconcile run, so reviews live in their own tables,
keyed by the sources a golden record was built from (`golden_key`). `reconcile` calls
`apply_reviews` to put approvals and corrections back on the rebuilt records. If the
sources behind a reviewed record change, the review is not applied: the new record
gets a `review_orphaned` finding pointing at the earlier review. Every action is written
to `review_events`.
"""

import hashlib
import json
from collections import Counter
from collections.abc import Iterable
from typing import Any

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from pipeline.config import Settings
from pipeline.domains import PackInfo, get_pack
from pipeline.store.models import (
    GoldenOverride,
    GoldenRecord,
    GoldenReview,
    MatchDecision,
    ReviewEvent,
)
from pipeline.validate.engine import validate_record
from pipeline.validate.types import Finding, RecordView

APPROVED = "approved"


def golden_key(source_keys: Iterable[str]) -> str:
    return hashlib.sha256("\n".join(sorted(source_keys)).encode()).hexdigest()[:32]


def actor(reviewer: str) -> str:
    name = " ".join(reviewer.split())
    if not name:
        raise ValueError("enter a reviewer name first")
    return f"human:{name}"[:64]


def _event(
    session: Session,
    who: str,
    action: str,
    pack: str,
    target: str,
    *,
    field: str | None = None,
    old: Any = None,
    new: Any = None,
    note: str = "",
) -> None:
    session.add(
        ReviewEvent(
            actor=who,
            action=action,
            pack=pack,
            target=target,
            field=field,
            old=old,
            new=new,
            note=note,
        )
    )


# --- re-validation ---------------------------------------------------------------------


def _verdict_findings(
    info: PackInfo, fields: dict[str, Any], meta: dict[str, Any], settings: Settings
) -> list[Finding]:
    view = RecordView(values=fields, meta=meta, method="reconciled")
    return [f for f in validate_record(info, view, settings).findings if f.rule != "escalated"]


def _status(findings: list[dict[str, Any]], approved: bool) -> str:
    if approved:
        return APPROVED
    severities = {f["severity"] for f in findings}
    if "reject" in severities:
        return "rejected"
    return "needs_review" if "review" in severities else "auto_approved"


def _set_fields(
    info: PackInfo,
    golden: GoldenRecord,
    changes: dict[str, Any],
    settings: Settings,
    who_by: dict[str, str],
) -> None:
    """Apply corrected values and re-check the record. Rule findings are recomputed;
    reconciliation findings (conflicts, cluster checks) are kept, except a source
    conflict on a field a person has now settled."""
    fields = dict(golden.fields)
    before = {f.rule for f in _verdict_findings(info, fields, _meta_of(golden), settings)}
    provenance = dict(golden.provenance)
    for name, value in changes.items():
        old = fields.get(name)
        fields[name] = value
        prov = dict(provenance.get(name) or {})
        if prov.get("rule", "").startswith("corrected by"):
            machine = prov.get("machine_value")
        else:
            machine = old
        provenance[name] = {
            **prov,
            "rule": f"corrected by {who_by[name]}",
            "machine_value": machine,
        }
    field_meta = _meta_of(golden)
    for name in changes:
        field_meta[name] = {"final_confidence": 1.0, "grounded": True, "flags": []}
    kept = [
        f
        for f in golden.findings
        if f["rule"] not in before
        and not (f["rule"] == "source_conflict" and set(f["fields"]) & set(changes))
    ]
    new = [f.as_dict() for f in _verdict_findings(info, fields, field_meta, settings)]
    golden.fields = fields
    golden.provenance = provenance
    golden.findings = new + kept
    golden.status = _status(golden.findings, golden.status == APPROVED)


def _meta_of(golden: GoldenRecord) -> dict[str, Any]:
    """Field metadata for re-validation: corrected fields are certain; others keep the
    confidence reconciliation gave them (stored in the provenance)."""
    out: dict[str, Any] = {}
    for name, prov in (golden.provenance or {}).items():
        if golden.fields.get(name) is None:
            continue
        sure = str(prov.get("rule", "")).startswith("corrected by")
        conf = 1.0 if sure else float(prov.get("confidence", 1.0))
        out[name] = {"final_confidence": conf, "grounded": True, "flags": []}
    return out


# --- applied by reconcile ----------------------------------------------------------------


def apply_reviews(
    session: Session, info: PackInfo, settings: Settings, goldens: list[GoldenRecord]
) -> Counter[str]:
    """Put stored approvals and corrections back on freshly rebuilt golden records."""
    counts: Counter[str] = Counter()
    by_key = {g.golden_key: g for g in goldens}
    reviews = session.scalars(select(GoldenReview).where(GoldenReview.pack == info.name)).all()
    overrides = session.scalars(
        select(GoldenOverride).where(GoldenOverride.pack == info.name)
    ).all()
    orphans: dict[str, list[str]] = {}  # old key -> descriptions
    by_golden: dict[str, list[GoldenOverride]] = {}
    for o in overrides:
        if o.golden_key in by_key:
            by_golden.setdefault(o.golden_key, []).append(o)
        else:
            orphans.setdefault(o.golden_key, []).append(
                f"{o.field} corrected to {o.value!r} by {o.reviewer}"
            )
    for key, items in by_golden.items():
        g = by_key[key]
        _set_fields(
            info,
            g,
            {o.field: o.value for o in items},
            settings,
            {o.field: o.reviewer for o in items},
        )
        counts["corrections"] += len(items)
    for r in reviews:
        hit = by_key.get(r.golden_key)
        if hit is not None:
            hit.status = APPROVED
            counts["approvals"] += 1
        else:
            orphans.setdefault(r.golden_key, []).append(f"approved by {r.reviewer}")
    if orphans:
        old_sources = {r.golden_key: set(r.source_keys) for r in reviews}
        for o in overrides:
            old_sources.setdefault(o.golden_key, set()).update(o.source_keys)
        for old_key, what in orphans.items():
            sources = old_sources.get(old_key, set())
            for g in goldens:
                if sources & set(g.source_keys):
                    g.findings = [
                        *g.findings,
                        {
                            "rule": "review_orphaned",
                            "severity": "review",
                            "fields": [],
                            "message": f"Reviewed before ({'; '.join(what)}), but the sources "
                            "behind this record have changed since. Check it again.",
                            "previous_key": old_key,
                        },
                    ]
                    if g.status != APPROVED:
                        g.status = "needs_review"
                    counts["orphaned"] += 1
    return counts


# --- reviewer actions --------------------------------------------------------------------


def _golden(session: Session, golden_id: int) -> GoldenRecord:
    g = session.get(GoldenRecord, golden_id)
    if g is None:
        raise ValueError(f"golden record {golden_id} not found (re-run reconcile?)")
    return g


def approve(session: Session, golden_id: int, reviewer: str, note: str = "") -> None:
    who = actor(reviewer)
    g = _golden(session, golden_id)
    row = session.scalars(
        select(GoldenReview).where(
            GoldenReview.pack == g.pack, GoldenReview.golden_key == g.golden_key
        )
    ).first()
    if row is None:
        session.add(
            GoldenReview(
                pack=g.pack,
                golden_key=g.golden_key,
                status=APPROVED,
                source_keys=g.source_keys,
                reviewer=who,
                note=note,
            )
        )
    old = g.status
    g.status = APPROVED
    _event(session, who, "approve", g.pack, g.golden_key, old=old, new=APPROVED, note=note)
    session.commit()


def unapprove(session: Session, golden_id: int, reviewer: str, note: str = "") -> None:
    who = actor(reviewer)
    g = _golden(session, golden_id)
    for row in session.scalars(
        select(GoldenReview).where(
            GoldenReview.pack == g.pack, GoldenReview.golden_key == g.golden_key
        )
    ):
        session.delete(row)
    g.status = _status(g.findings, approved=False)
    _event(session, who, "unapprove", g.pack, g.golden_key, old=APPROVED, new=g.status, note=note)
    session.commit()


def parse_value(info: PackInfo, field: str, text: Any) -> Any:
    """A reviewer's input -> the field's JSON form, validated by the pack schema.
    Raises ValueError with a readable message."""
    if field not in info.schema.model_fields:
        raise ValueError(f"unknown field {field!r}")
    if isinstance(text, str):
        text = text.strip()
        if text == "":
            text = None
        elif text[:1] in "[{":
            try:
                text = json.loads(text)
            except json.JSONDecodeError as e:
                raise ValueError(f"not valid JSON: {e}") from e
    annotation: Any = info.schema.model_fields[field].annotation
    adapter: TypeAdapter[Any] = TypeAdapter(annotation)
    try:
        value = adapter.validate_python(text)
    except ValidationError as e:
        raise ValueError(e.errors()[0]["msg"]) from e
    return adapter.dump_python(value, mode="json")


def correct(
    session: Session,
    golden_id: int,
    field: str,
    text: Any,
    reviewer: str,
    settings: Settings,
    note: str = "",
) -> list[dict[str, Any]]:
    """Store a corrected value, apply it, re-check the record; returns its findings."""
    who = actor(reviewer)
    g = _golden(session, golden_id)
    info = get_pack(g.pack)
    value = parse_value(info, field, text)
    old = g.fields.get(field)
    row = session.scalars(
        select(GoldenOverride).where(
            GoldenOverride.pack == g.pack,
            GoldenOverride.golden_key == g.golden_key,
            GoldenOverride.field == field,
        )
    ).first()
    if row is None:
        session.add(
            GoldenOverride(
                pack=g.pack,
                golden_key=g.golden_key,
                field=field,
                value=value,
                source_keys=g.source_keys,
                reviewer=who,
                note=note,
            )
        )
    else:
        row.value, row.reviewer, row.note = value, who, note
    _set_fields(info, g, {field: value}, settings, {field: who})
    _event(
        session, who, "correct", g.pack, g.golden_key, field=field, old=old, new=value, note=note
    )
    session.commit()
    return list(g.findings)


def reapply(
    session: Session, golden_id: int, old_key: str, reviewer: str, settings: Settings
) -> int:
    """Carry an orphaned review's corrections over to this record (a person checked it)."""
    who = actor(reviewer)
    g = _golden(session, golden_id)
    rows = session.scalars(
        select(GoldenOverride).where(
            GoldenOverride.pack == g.pack, GoldenOverride.golden_key == old_key
        )
    ).all()
    for o in rows:
        correct(
            session,
            golden_id,
            o.field,
            o.value,
            reviewer,
            settings,
            note=f"re-applied from an earlier review: {o.note}".strip(": "),
        )
    g.findings = [
        f
        for f in g.findings
        if not (f["rule"] == "review_orphaned" and f.get("previous_key") == old_key)
    ]
    g.status = _status(g.findings, g.status == APPROVED)
    _event(session, who, "reapply", g.pack, g.golden_key, old=old_key, new=len(rows))
    session.commit()
    return len(rows)


def decide_match(
    session: Session, pack: str, a: str, b: str, same: bool, reviewer: str, note: str = ""
) -> None:
    """A person decides whether two (normalized) names are one organization. Takes effect
    on the next reconcile run, and is never overwritten by the model (D-036)."""
    who = actor(reviewer)
    a, b = sorted((a, b))
    row = session.scalars(
        select(MatchDecision).where(
            MatchDecision.pack == pack,
            MatchDecision.kind == "entity",
            MatchDecision.a == a,
            MatchDecision.b == b,
        )
    ).first()
    decision = "same" if same else "different"
    old = None
    if row is None:
        session.add(
            MatchDecision(
                pack=pack,
                kind="entity",
                a=a,
                b=b,
                decision=decision,
                decided_by=who,
                confidence=1.0,
                reason=note or "reviewer",
            )
        )
    else:
        old = f"{row.decision} ({row.decided_by})"
        row.decision, row.decided_by, row.confidence = decision, who, 1.0
        row.reason = note or "reviewer"
    _event(session, who, "match", pack, f"{a} | {b}", old=old, new=decision, note=note)
    session.commit()
