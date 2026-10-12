"""Survivorship: pick each golden field's value from a cluster of linked records, and
record every disagreement (D-035). Pure logic."""

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from pipeline.reconcile.names import fold
from pipeline.reconcile.types import Candidate, ReconcileSpec

ARITHMETIC_RULES = frozenset({"totals_add_up", "line_items_sum", "line_amount"})


def comparable(v: Any) -> Any:
    """A key under which values that mean the same thing compare equal."""
    if v is None:
        return None
    if isinstance(v, list):
        return len(v)
    try:
        return Decimal(str(v)).normalize()
    except (InvalidOperation, ValueError):
        return " ".join(fold(str(v)).split())


@dataclass
class FieldChoice:
    value: Any
    rule: str
    winner: int | None  # extracted_records.id that supplied the value
    support: list[int] = field(default_factory=list)  # ids agreeing with the value
    alternatives: list[dict[str, Any]] = field(default_factory=list)  # [{value, ids, sources}]
    variants: list[Any] = field(default_factory=list)  # other spellings of the chosen value


def _groups(cs: list[Candidate], name: str, spec: ReconcileSpec) -> list[list[Candidate]]:
    """Group candidates whose values mean the same thing: equal under `comparable`, then
    joined by the pack's equivalence for this field (typos, formats, nicknames)."""
    exact: dict[Any, list[Candidate]] = defaultdict(list)
    for c in cs:
        v = c.values.get(name)
        if v is not None and v != []:
            exact[comparable(v)].append(c)
    groups = list(exact.values())
    same = spec.equivalent.get(name)
    if same is None:
        return groups
    merged: list[list[Candidate]] = []
    for g in sorted(groups, key=len, reverse=True):
        v = g[0].values.get(name)
        home = next((m for m in merged if any(same(v, x.values.get(name)) for x in m)), None)
        if home is None:
            merged.append(list(g))
        else:
            home.extend(g)
    return merged


def _conf(c: Candidate, name: str) -> float:
    m = c.meta.get(name) or {}
    v = m.get("final_confidence")
    return float(v) if isinstance(v, int | float) else 0.0


def _best(cs: list[Candidate], name: str, spec: ReconcileSpec) -> Candidate:
    """The member whose spelling represents the group: the pack's preferred form, then
    the most common spelling, then the most trusted source."""
    counts = Counter(comparable(c.values.get(name)) for c in cs)
    return max(
        cs,
        key=lambda c: (
            spec.display_rank(name, c.values.get(name)),
            counts[comparable(c.values.get(name))],
            spec.trust(c),
            _conf(c, name),
            -c.id,
        ),
    )


def _when(c: Candidate, spec: ReconcileSpec) -> date:
    if c.when is not None:
        return c.when
    return date.max if spec.undated_is_current(c) else date.min


RULES = {
    "amount": "most sources agree, arithmetic consistent",
    "recent": "most recent source",
    "recent_entity": "most recent source",
    "system_of_record": "system of record",
    "richest": "most complete source",
}


def choose(name: str, cs: list[Candidate], spec: ReconcileSpec) -> FieldChoice:
    groups = _groups(cs, name, spec)
    if not groups:
        return FieldChoice(None, "no source states it", None)
    policy = spec.policies.get(name, "majority")

    def ranked(members: list[Candidate]) -> tuple[Any, ...]:
        trust = max(spec.trust(c) for c in members)
        conf = max(_conf(c, name) for c in members)
        if policy == "amount":
            consistent = sum(not (set(c.findings) & ARITHMETIC_RULES) for c in members)
            return (len(members), consistent, trust, conf)
        if policy in ("recent", "recent_entity"):
            return (max(_when(c, spec) for c in members), trust, len(members))
        if policy == "system_of_record":
            return (any(spec.system_of_record(c) for c in members), len(members), trust)
        if policy == "richest":
            size = max(len(v) if isinstance(v := c.values.get(name), list) else 0 for c in members)
            return (size, trust)
        return (len(members), trust, conf)

    order = sorted(groups, key=ranked, reverse=True)
    winners = order[0]
    best = _best(winners, name, spec)
    rule = RULES.get(policy, "most sources agree")
    if len(groups) == 1:
        rule = "all sources agree" if len(winners) > 1 else "single source"
    value = best.values.get(name)
    variants = []
    if not isinstance(value, list):
        variants = sorted({str(c.values.get(name)) for c in winners} - {str(value)})
    alternatives = [
        {
            "value": _best(g, name, spec).values.get(name),
            "ids": [c.id for c in g],
            "sources": sorted({c.source for c in g}),
        }
        for g in order[1:]
    ]
    return FieldChoice(value, rule, best.id, [c.id for c in winners], alternatives, variants)
