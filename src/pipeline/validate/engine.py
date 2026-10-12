"""Turn an extracted record into a status plus reasons (D-029).

Pack rules check the domain logic; the engine adds generic checks on the
extraction itself (confidence, grounding, invalid or missing values). Status:
any `reject` finding -> rejected; else any `review` -> needs_review; else
auto_approved.
"""

import hashlib
from dataclasses import dataclass

from pipeline.config import Settings
from pipeline.domains import PackInfo
from pipeline.extract.schema import required_fields
from pipeline.validate.types import Finding, RecordView, Status


@dataclass(frozen=True)
class Verdict:
    status: Status
    findings: list[Finding]

    @property
    def reasons(self) -> list[str]:
        return [f.message for f in self.findings if f.severity != "info"]


def validator_version(pack: PackInfo, settings: Settings) -> str:
    """Rules version + thresholds: a change to either means results must be recomputed."""
    key = f"{pack.rules_version}|{settings.review_confidence}"
    return f"{pack.rules_version}-{hashlib.sha256(key.encode()).hexdigest()[:6]}"


def extraction_findings(pack: PackInfo, rec: RecordView, settings: Settings) -> list[Finding]:
    """Generic checks on how trustworthy the extraction itself is."""
    out: list[Finding] = []
    for name in required_fields(pack.schema):
        if rec.get(name) is None:
            out.append(
                Finding("required_missing", "review", f"Required field {name} is missing.", (name,))
            )
    for name, m in rec.meta.items():
        if rec.get(name) is None:
            flags = m.get("flags") or []
            if any(str(f).startswith("invalid_value") for f in flags):
                out.append(
                    Finding(
                        "invalid_value",
                        "review",
                        f"{name}: the source value could not be read "
                        f"({flags[0].split(': ', 1)[-1]}).",
                        (name,),
                    )
                )
            if "missing_year" in flags:
                out.append(
                    Finding(
                        "missing_year",
                        "review",
                        f"{name}: the date has no year in the source.",
                        (name,),
                    )
                )
            continue
        conf = m.get("final_confidence")
        if isinstance(conf, int | float) and conf < settings.review_confidence:
            out.append(
                Finding(
                    "low_confidence", "review", f"{name}: low confidence ({conf:.2f}).", (name,)
                )
            )
        if m.get("grounded") is False:
            out.append(
                Finding(
                    "not_in_source",
                    "review",
                    f"{name}: the value {rec.get(name)!r} was not found in the source text.",
                    (name,),
                )
            )
    if any("escalation failed" in r for r in rec.extraction_reasons):
        out.append(
            Finding(
                "extraction_degraded",
                "review",
                "The bigger model failed on this document; this result comes from "
                "the first model only.",
            )
        )
    if rec.escalated:
        out.append(Finding("escalated", "info", "Extracted by the escalation model."))
    return out


def validate_record(pack: PackInfo, rec: RecordView, settings: Settings) -> Verdict:
    findings = extraction_findings(pack, rec, settings)
    for rule in pack.rules:
        findings.extend(rule(rec))
    # Same rule on the same fields reported once (e.g. missing + pack identity rule).
    seen: set[tuple[str, tuple[str, ...]]] = set()
    unique = []
    for f in findings:
        if (f.rule, f.fields) not in seen:
            seen.add((f.rule, f.fields))
            unique.append(f)
    severities = {f.severity for f in unique}
    status: Status = (
        "rejected"
        if "reject" in severities
        else "needs_review"
        if "review" in severities
        else "auto_approved"
    )
    return Verdict(status, unique)
