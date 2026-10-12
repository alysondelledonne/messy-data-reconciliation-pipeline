"""Types shared by the validation engine and the domain packs' rules (D-030)."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

Severity = Literal["reject", "review", "info"]
Status = Literal["auto_approved", "needs_review", "rejected"]


@dataclass(frozen=True)
class Finding:
    rule: str
    severity: Severity
    message: str  # a sentence a reviewer can act on
    fields: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "rule": self.rule,
            "severity": self.severity,
            "message": self.message,
            "fields": list(self.fields),
        }


@dataclass(frozen=True)
class RecordView:
    """What a rule sees: one extracted record plus how it was produced."""

    values: Mapping[str, Any]
    meta: Mapping[str, Any] = field(default_factory=dict)  # per-field confidence/evidence/flags
    flags: tuple[str, ...] = ()  # record-level flags (e.g. missing_required:x)
    method: str = "llm"  # llm | column_mapping
    source_format: str = ""
    escalated: bool = False
    extraction_reasons: tuple[str, ...] = ()

    def get(self, name: str) -> Any:
        return self.values.get(name)


Rule = Callable[[RecordView], list[Finding]]
