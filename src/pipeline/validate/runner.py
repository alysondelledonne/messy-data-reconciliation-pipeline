"""Validate every current extracted record of a pack and store the verdicts."""

from collections import Counter
from dataclasses import dataclass, field

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from pipeline.config import Settings
from pipeline.domains import get_pack
from pipeline.extract.runner import extractor_version
from pipeline.store.models import (
    ExtractedRecord,
    ExtractionResult,
    RawRecordRow,
    SourceFile,
    Validation,
)
from pipeline.validate.engine import validate_record, validator_version
from pipeline.validate.types import RecordView


@dataclass
class ValidateStats:
    records: int = 0
    by_status: Counter[str] = field(default_factory=Counter)
    by_rule: Counter[str] = field(default_factory=Counter)
    by_format: Counter[tuple[str, str]] = field(default_factory=Counter)

    def summary(self, top: int = 12) -> str:
        lines = [f"validated {self.records} records: {dict(self.by_status)}"]
        if self.by_rule:
            lines.append("most common reasons (rule: records):")
            lines += [f"  {rule}: {n}" for rule, n in self.by_rule.most_common(top)]
        formats = sorted({f for f, _ in self.by_format})
        if formats:
            lines.append("by source format (auto_approved / needs_review / rejected):")
            for fmt in formats:
                counts = [
                    self.by_format[(fmt, s)] for s in ("auto_approved", "needs_review", "rejected")
                ]
                lines.append(f"  {fmt:<12} {counts[0]:>5} / {counts[1]:>5} / {counts[2]:>5}")
        return "\n".join(lines)


def validate_pack(session: Session, pack: str, settings: Settings) -> ValidateStats:
    """Recompute verdicts for the current extractor version (cheap, deterministic)."""
    info = get_pack(pack)
    version = validator_version(info, settings)
    rows = session.execute(
        select(ExtractedRecord, ExtractionResult, SourceFile.format)
        .join(ExtractionResult, ExtractionResult.id == ExtractedRecord.result_id)
        .join(RawRecordRow, RawRecordRow.id == ExtractionResult.raw_record_id)
        .join(SourceFile, SourceFile.id == RawRecordRow.source_file_id)
        .where(
            ExtractionResult.pack == pack,
            ExtractionResult.extractor_version == extractor_version(info),
        )
        .order_by(ExtractedRecord.id)
    ).all()
    session.execute(
        delete(Validation).where(Validation.pack == pack, Validation.validator_version == version)
    )
    stats = ValidateStats()
    for rec, result, fmt in rows:
        view = RecordView(
            values=rec.fields,
            meta=rec.field_meta,
            flags=tuple(rec.flags),
            method=result.method,
            source_format=fmt,
            escalated=result.escalated,
            extraction_reasons=tuple(result.reasons),
        )
        verdict = validate_record(info, view, settings)
        session.add(
            Validation(
                extracted_record_id=rec.id,
                pack=pack,
                validator_version=version,
                status=verdict.status,
                findings=[f.as_dict() for f in verdict.findings],
            )
        )
        stats.records += 1
        stats.by_status[verdict.status] += 1
        stats.by_format[(fmt, verdict.status)] += 1
        for rule in {f.rule for f in verdict.findings if f.severity != "info"}:
            stats.by_rule[rule] += 1
    session.commit()
    return stats
