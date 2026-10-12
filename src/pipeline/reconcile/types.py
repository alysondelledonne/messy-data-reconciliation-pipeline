"""Types shared by the reconciliation engine and the domain packs' policies (D-034, D-035)."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from pipeline.validate.types import Finding


@dataclass
class Candidate:
    """One extracted record, as reconciliation sees it."""

    id: int  # extracted_records.id
    values: Mapping[str, Any]
    meta: Mapping[str, Any]
    source: str  # source file path
    format: str
    kind: str  # raw record kind
    when: date | None  # date of the source, for recency (None: undated)
    status: str | None = None  # validation status, if validated
    findings: tuple[str, ...] = ()  # validation rule names that fired
    entity: int | None = None  # assigned by entity resolution
    source_key: str = ""  # "path#location#record index": stable across re-extraction


@dataclass(frozen=True)
class ReconcileSpec:
    """Everything domain-specific about reconciling one pack."""

    entity_field: str  # field naming the organization (vendor_name / company)
    entity_kind: str  # "vendor" / "company"
    # (keys that prove two names are the same org when shared,
    #  family keys that suggest different orgs when the sets are disjoint)
    evidence: Callable[[Candidate], tuple[set[str], set[str]]]
    strong_keys: Callable[[Candidate], list[str]]  # equal key -> same record
    soft_key: Callable[[Candidate], str | None] = lambda c: None  # blocking key for soft matches
    soft_match: Callable[[Candidate, Candidate], bool] = lambda a, b: False
    compatible: Callable[[Candidate, Candidate], bool] = lambda a, b: True
    # Key for records too sparse for the soft key (no organization named): such a record
    # joins the one cluster sharing this key, and stays alone if several do.
    fallback_key: Callable[[Candidate], str | None] = lambda c: None
    policies: Mapping[str, str] = field(default_factory=dict)  # field -> survivorship strategy
    # field -> "these two values are the same, written differently" (formats, typos,
    # nicknames). Variants are grouped before the policy picks a group; not conflicts.
    equivalent: Mapping[str, Callable[[Any, Any], bool]] = field(default_factory=dict)
    # Within a group of variants, which spelling to show (higher is better), e.g. a phone
    # number in E.164 form over a national one.
    display_rank: Callable[[str, Any], int] = lambda name, value: 0
    trust: Callable[[Candidate], int] = lambda c: 1
    # Clean-up that needs the whole golden record (a national phone number gets the
    # country code of the chosen country). Returns only the fields it changes.
    finalize: Callable[[Mapping[str, Any]], dict[str, Any]] = lambda fields: {}
    material: frozenset[str] = frozenset()  # conflicts on these fields go to review
    # An undated source of this kind is a current snapshot (e.g. a CRM export); other
    # undated sources count as the oldest.
    undated_is_current: Callable[[Candidate], bool] = lambda c: False
    system_of_record: Callable[[Candidate], bool] = lambda c: False
    cluster_findings: Callable[[list[Candidate]], list[Finding]] = lambda cs: []
