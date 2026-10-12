"""Reconcile a pack: canonical entities, golden records, conflicts, statuses (D-033..D-036)."""

import contextlib
import json
import re
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from pipeline.config import Settings
from pipeline.domains import PackInfo, get_pack
from pipeline.extract.client import LLMClient, LLMRequest
from pipeline.extract.runner import CachedCaller, RunStats, extractor_version
from pipeline.ingest.normalize import parse_date
from pipeline.reconcile.entities import NameGroup, PairVerdict, name_groups, resolve
from pipeline.reconcile.names import normalize
from pipeline.reconcile.survive import FieldChoice, choose
from pipeline.reconcile.types import Candidate, ReconcileSpec
from pipeline.store.models import (
    Entity,
    ExtractedRecord,
    ExtractionResult,
    GoldenRecord,
    MatchDecision,
    RawRecordRow,
    RecordLink,
    SourceFile,
    Validation,
)
from pipeline.validate.engine import validate_record, validator_version
from pipeline.validate.types import Finding, RecordView, Status

# --- source dates (for "most recent wins") ------------------------------------------------

_DATED_LINE = re.compile(
    r"^\s*(?:#+\s*)?(?:.*?\b(?:date|updated|notes?|visit|sync)\b[:\s]*)?(.+)$", re.I
)
_MONTH_YEAR = re.compile(r"\b([A-Za-z]{3,9})\.?\s+(\d{4})\b")
_ISO_IN_NAME = re.compile(r"(20\d{2})(\d{2})(\d{2})")


def _date_in(text: str) -> date | None:
    """A date anywhere in a short header line ('Date: 04 April 2025', 'Last updated May 2024',
    '# Site visit: 2023-03-15', '03/14/2023 call notes')."""
    for whole in (text.strip(), text.split(":", 1)[-1].strip(" .#")):
        pd = parse_date(whole)
        if pd and pd.value:
            return pd.value
    for token in re.split(r"[:,]\s+|\s{2,}|\s(?=\d)|^", text.strip()):
        for candidate in (token, token.rsplit(" ", 2)[0]):
            pd = parse_date(candidate.strip(" .#"))
            if pd and pd.value:
                return pd.value
    m = _MONTH_YEAR.search(text)
    if m:
        pd = parse_date(f"{m[1]} 1, {m[2]}")
        if pd and pd.value:
            return pd.value
    for word in re.findall(r"[\w/.-]+", text):
        pd = parse_date(word)
        if pd and pd.value:
            return pd.value
    return None


def _record_date(raw: RawRecordRow) -> date | None:
    for value in raw.normalized.values():
        if isinstance(value, dict) and value.get("type") == "date" and value.get("value"):
            return date.fromisoformat(value["value"])
    obj = raw.raw.get("object") if isinstance(raw.raw, dict) else None
    for v in (obj or {}).values() if isinstance(obj, dict) else ():
        if isinstance(v, int) and 946684800 <= v <= 4102444800:  # epoch seconds 2000..2100
            return datetime.fromtimestamp(v, tz=UTC).date()
    if raw.kind == "email_quoted":
        return _date_in(raw.text.splitlines()[0]) if raw.text else None
    return None


def _file_dates(session: Session, file_ids: set[int]) -> dict[int, date]:
    """One date per source file: an email's Date header, a document's 'Date:' or
    'Last updated' line, the first line of a note, or a date in the file name."""
    out: dict[int, date] = {}
    rows = session.execute(
        select(
            RawRecordRow.source_file_id,
            RawRecordRow.kind,
            RawRecordRow.text,
            RawRecordRow.normalized,
            SourceFile.path,
        )
        .join(SourceFile)
        .where(RawRecordRow.source_file_id.in_(file_ids))
        .order_by(RawRecordRow.id)
    ).all()
    looked: Counter[int] = Counter()
    for file_id, kind, text, normalized, path in rows:
        looked[file_id] += 1
        if file_id in out or looked[file_id] > 3:  # a date line sits near the top
            continue
        if kind == "email_body" and (d := normalized.get("Date")):
            out[file_id] = date.fromisoformat(d["value"])
        elif kind in ("paragraph", "document") and text:
            header = text.splitlines()[0]
            if (kind == "document" or re.search(r"\b(date|updated)\b", header, re.I)) and (
                found := _date_in(header)
            ):
                out[file_id] = found
        if file_id not in out and (m := _ISO_IN_NAME.search(path)):
            with contextlib.suppress(ValueError):
                out[file_id] = date(int(m[1]), int(m[2]), int(m[3]))
    return out


# --- loading ----------------------------------------------------------------------------


def load_candidates(session: Session, info: PackInfo, settings: Settings) -> list[Candidate]:
    """Current extracted records of a pack, minus those validation rejected."""
    vversion = validator_version(info, settings)
    rows = session.execute(
        select(ExtractedRecord, RawRecordRow, SourceFile, Validation)
        .join(ExtractionResult, ExtractionResult.id == ExtractedRecord.result_id)
        .join(RawRecordRow, RawRecordRow.id == ExtractionResult.raw_record_id)
        .join(SourceFile, SourceFile.id == RawRecordRow.source_file_id)
        .outerjoin(
            Validation,
            (Validation.extracted_record_id == ExtractedRecord.id)
            & (Validation.validator_version == vversion),
        )
        .where(
            ExtractionResult.pack == info.name,
            ExtractionResult.extractor_version == extractor_version(info),
        )
        .order_by(ExtractedRecord.id)
    ).all()
    file_dates = _file_dates(session, {sf.id for _, _, sf, _ in rows})
    out = []
    for rec, raw, sf, val in rows:
        if val is not None and val.status == "rejected":
            continue
        out.append(
            Candidate(
                id=rec.id,
                values=rec.fields,
                meta=rec.field_meta,
                source=sf.path,
                format=sf.format,
                kind=raw.kind,
                when=_record_date(raw) or file_dates.get(sf.id),
                status=val.status if val else None,
                findings=tuple(
                    f["rule"] for f in (val.findings if val else []) if f["severity"] != "info"
                ),
            )
        )
    return out


# --- close calls: human decisions, then the model, then the review queue -----------------

ADJUDICATION_SYSTEM = """\
You decide whether two spellings name the same organization, for a data reconciliation \
pipeline. The names come from messy business records: typos, abbreviations, missing legal \
suffixes and cut-off text are common, but so are genuinely different organizations with \
confusingly similar names (a sister company, a supplier with a similar name). Use the \
evidence: shared identifiers mean the same organization; different email domains or \
invoice-number prefixes suggest different ones. If the evidence doesn't settle it, say so \
with a low confidence rather than guessing."""

ADJUDICATION_SCHEMA = {
    "type": "object",
    "properties": {
        "same": {"type": "boolean"},
        "confidence": {"type": "number", "description": "0-1"},
        "reason": {"type": "string", "description": "One sentence a reviewer can check"},
    },
    "required": ["same", "confidence", "reason"],
    "additionalProperties": False,
}


class Adjudicator:
    def __init__(
        self, session: Session, info: PackInfo, settings: Settings, client: LLMClient | None
    ) -> None:
        self.session, self.info, self.settings = session, info, settings
        self.stats = RunStats()
        self.caller = (
            CachedCaller(session, client, info.name, settings, self.stats) if client else None
        )
        self.decisions = {
            (d.a, d.b): d
            for d in session.scalars(
                select(MatchDecision).where(
                    MatchDecision.pack == info.name, MatchDecision.kind == "entity"
                )
            )
        }
        self.counts: Counter[str] = Counter()

    def __call__(self, v: PairVerdict, ga: NameGroup, gb: NameGroup) -> str:
        a, b = sorted((v.a, v.b))
        existing = self.decisions.get((a, b))
        if existing is not None and existing.decision != "pending":
            self.counts[f"decided_before:{existing.decided_by.split(':')[0]}"] += 1
            return "merge" if existing.decision == "same" else "separate"
        if self.caller is not None and self.settings.match_llm:
            outcome = self._ask(v, ga, gb, a, b)
            if outcome:
                return outcome
        self._store(a, b, "pending", "system", None, f"needs a human: {v.reason}")
        self.counts["pending"] += 1
        return "pending"

    def _ask(self, v: PairVerdict, ga: NameGroup, gb: NameGroup, a: str, b: str) -> str | None:
        assert self.caller is not None

        def describe(g: NameGroup) -> dict[str, Any]:
            return {
                "spellings": [s for s, _ in g.spellings.most_common(5)],
                "mentions": g.count,
                "identifiers": sorted(g.same_keys)[:10],
                "identifier_families": sorted(g.family_keys)[:10],
            }

        content = json.dumps(
            {
                "kind": self.info.reconcile.entity_kind if self.info.reconcile else "organization",
                "name_a": describe(ga),
                "name_b": describe(gb),
                "similarity": round(v.score),
                "words_without_counterpart": list(v.guard),
            },
            indent=1,
            ensure_ascii=False,
        )
        req = LLMRequest(
            model=self.settings.extract_model,
            system=ADJUDICATION_SYSTEM,
            content=[{"type": "text", "text": content}],
            schema=ADJUDICATION_SCHEMA,
            effort=self.settings.extract_effort,
            max_tokens=4000,
            purpose="entity_match",
            meta={"a_spellings": list(ga.spellings), "b_spellings": list(gb.spellings)},
        )
        (out,) = self.caller.run([req])
        data = out.response.data
        if out.response.status != "ok" or not data:
            return None
        conf = float(data.get("confidence") or 0)
        reason = str(data.get("reason") or "")
        model = out.response.model_served
        if conf < self.settings.match_llm_confidence:
            self._store(
                a,
                b,
                "pending",
                f"llm:{model}",
                conf,
                f"model unsure ({'same' if data.get('same') else 'different'}): {reason}",
            )
            self.counts["llm_unsure"] += 1
            return "pending"
        decision = "same" if data.get("same") else "different"
        self._store(a, b, decision, f"llm:{model}", conf, reason)
        self.counts[f"llm_{decision}"] += 1
        return "merge" if decision == "same" else "separate"

    def _store(
        self, a: str, b: str, decision: str, by: str, conf: float | None, reason: str
    ) -> None:
        row = self.decisions.get((a, b))
        if row is None:
            row = MatchDecision(
                pack=self.info.name,
                kind="entity",
                a=a,
                b=b,
                decision=decision,
                decided_by=by,
                confidence=conf,
                reason=reason,
            )
            self.session.add(row)
            self.decisions[(a, b)] = row
        elif not row.decided_by.startswith("human"):
            row.decision, row.decided_by, row.confidence, row.reason = decision, by, conf, reason


# --- record linking ----------------------------------------------------------------------


class _Clusters:
    """Union-find over candidates that keeps each cluster's members."""

    def __init__(self, cands: list[Candidate]) -> None:
        self.p = {c.id: c.id for c in cands}
        self.members: dict[int, list[Candidate]] = {c.id: [c] for c in cands}

    def find(self, x: int) -> int:
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[ra] = rb
            self.members[rb] += self.members.pop(ra)

    def of(self, x: int) -> list[Candidate]:
        return self.members[self.find(x)]


Matcher = Callable[[list[Candidate], list[Candidate]], bool]


@dataclass
class Linking:
    clusters: list[list[Candidate]]
    reasons: dict[int, str]  # candidate id -> why it joined its cluster
    ambiguous: set[int]  # candidates that could belong to more than one record


def link_records(spec: ReconcileSpec, cands: list[Candidate]) -> Linking:
    """Cluster candidates into real-world records:
    1. equal strong keys (invoice number, email, phone) join;
    2. within a soft-key block (same organization and name, or vendor and total), records
       that match and are compatible with every member of the other cluster join, unless
       they fit two clusters that are incompatible with each other (namesakes);
    3. records too sparse for a soft key join the single cluster sharing their fallback
       key."""
    uf = _Clusters(cands)
    reasons: dict[int, str] = {c.id: "seed" for c in cands}
    ambiguous: set[int] = set()

    def fits(x: list[Candidate], y: list[Candidate]) -> bool:
        return all(spec.compatible(a, b) for a in x for b in y)

    seen: dict[str, list[int]] = defaultdict(list)
    for c in cands:
        for key in spec.strong_keys(c):
            # A shared key joins records only when they're compatible (a phone number
            # that passed from one person to another doesn't make them one person).
            for other in seen[key]:
                if uf.find(other) != uf.find(c.id) and fits(uf.of(other), uf.of(c.id)):
                    if reasons[c.id] == "seed":
                        reasons[c.id] = key.split(":", 1)[0]
                    uf.union(c.id, other)
            seen[key].append(c.id)

    def merge(keep: int, other: int, why: str) -> None:
        """Join two clusters; the joining cluster's seed records why."""
        for c in uf.of(other):
            if reasons[c.id] == "seed":
                reasons[c.id] = why
        uf.union(other, keep)

    def join(groups: list[list[Candidate]], match: Matcher, why: str) -> None:
        roots = sorted({uf.find(c.id) for g in groups for c in g})
        partners = {
            r: [o for o in roots if o != r and match(uf.members[r], uf.members[o])] for r in roots
        }
        # A cluster that fits two clusters which can't be one record (namesakes) could be
        # either: it stays out of this block's merges.
        unsure = {
            r
            for r, ps in partners.items()
            if any(
                not fits(uf.members[x], uf.members[y])
                for i, x in enumerate(ps)
                for y in ps[i + 1 :]
            )
        }
        for r in unsure:
            ambiguous.update(c.id for c in uf.members[r])
        for r in roots:
            for o in partners[r]:
                if r in unsure or o in unsure:
                    continue
                if uf.find(o) != uf.find(r) and fits(uf.of(r), uf.of(o)):
                    merge(r, o, why)

    by_soft: dict[str, list[Candidate]] = defaultdict(list)
    for c in cands:
        if (k := spec.soft_key(c)) is not None:
            by_soft[k].append(c)
    for block in by_soft.values():
        keyset = {c.id for c in block}

        def soft(x: list[Candidate], y: list[Candidate], keyset: set[int] = keyset) -> bool:
            return fits(x, y) and any(
                spec.soft_match(a, b) for a in x if a.id in keyset for b in y if b.id in keyset
            )

        join([[c] for c in block], soft, "soft")

    by_fallback: dict[str, list[Candidate]] = defaultdict(list)
    for c in cands:
        if (k := spec.fallback_key(c)) is not None:
            by_fallback[k].append(c)
    for block in by_fallback.values():
        roots = {uf.find(c.id) for c in block}
        sparse = {r for r in roots if all(spec.soft_key(c) is None for c in uf.members[r])}
        rich = roots - sparse
        leftover = []
        for r in sorted(sparse):
            fitting = [o for o in rich if fits(uf.of(r), uf.of(o))]
            if len(fitting) == 1:
                merge(fitting[0], r, "name_only")
            elif len(fitting) > 1:
                ambiguous.update(c.id for c in uf.of(r))
            else:
                leftover.append(r)
        if not rich and len(leftover) > 1:  # only sparse mentions of this name: one person
            for r in leftover[1:]:
                if fits(uf.of(leftover[0]), uf.of(r)):
                    merge(leftover[0], r, "name_only")
    clusters = list(uf.members.values())
    return Linking(clusters, reasons, ambiguous)


# --- golden records ---------------------------------------------------------------------


@dataclass
class ReconcileStats:
    candidates: int = 0
    entities: int = 0
    aliases: int = 0
    golden: int = 0
    multi_source: int = 0
    by_status: Counter[str] = field(default_factory=Counter)
    by_rule: Counter[str] = field(default_factory=Counter)
    conflicts: Counter[str] = field(default_factory=Counter)
    links: Counter[str] = field(default_factory=Counter)
    matches: Counter[str] = field(default_factory=Counter)
    llm_cost: float = 0.0

    def summary(self) -> str:
        lines = [
            f"{self.candidates} extracted records -> {self.golden} golden records "
            f"({self.multi_source} from 2+ sources)",
            f"{self.entities} entities from {self.aliases} spellings; close calls: "
            f"{dict(self.matches) or 'none'} (model cost ${self.llm_cost:.4f})",
            f"links by reason: {dict(self.links)}",
            f"statuses: {dict(self.by_status)}",
        ]
        if self.conflicts:
            lines.append(
                "conflicts by field: "
                + ", ".join(f"{k}={v}" for k, v in self.conflicts.most_common())
            )
        if self.by_rule:
            lines.append(
                "review reasons: " + ", ".join(f"{k}={v}" for k, v in self.by_rule.most_common(10))
            )
        return "\n".join(lines)


def _money(v: Any) -> str:
    try:
        return f"{float(v):,.2f}"
    except (TypeError, ValueError):
        return str(v)


def build_golden(
    info: PackInfo, cluster: list[Candidate], entity_names: dict[int, str], settings: Settings
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], Status, list[Finding], int | None]:
    spec = info.reconcile
    assert spec is not None
    fields: dict[str, Any] = {}
    provenance: dict[str, Any] = {}
    conflicts: list[dict[str, Any]] = []
    meta: dict[str, Any] = {}
    findings: list[Finding] = []
    by_id = {c.id: c for c in cluster}
    entity_id: int | None = None
    for name in info.schema.model_fields:
        choice: FieldChoice = choose(name, cluster, spec)
        value = choice.value
        policy = spec.policies.get(name)
        if policy in ("entity", "recent_entity") and choice.winner is not None:
            entity_id = by_id[choice.winner].entity
            if entity_id is not None:
                value = entity_names.get(entity_id, value)
        fields[name] = value
        provenance[name] = {
            "rule": choice.rule,
            "winner": choice.winner,
            "support": choice.support,
            "alternatives": choice.alternatives,
            "variants": choice.variants,
        }
        support = [by_id[i] for i in choice.support]
        confs = [float((c.meta.get(name) or {}).get("final_confidence") or 0) for c in support]
        if value is not None:
            meta[name] = {
                "final_confidence": max(confs) if confs else 0.0,
                "grounded": True if support else None,
                "flags": [],
            }
        alts = [
            a
            for a in choice.alternatives
            if not (
                policy in ("entity", "recent_entity")
                and _same_entity(a, by_id, by_id[choice.winner] if choice.winner else None)
            )
        ]
        if alts and policy != "richest":
            entry = {
                "field": name,
                "chosen": value,
                "rule": choice.rule,
                "chosen_sources": sorted({c.source for c in support}),
                "alternatives": alts,
            }
            conflicts.append(entry)
            if name in spec.material:
                shown = _money if name in ("total", "subtotal", "tax") else str
                others = "; ".join(f"{shown(a['value'])} ({', '.join(a['sources'])})" for a in alts)
                findings.append(
                    Finding(
                        "source_conflict",
                        "review",
                        f"Sources disagree on {name}: chose {shown(value)} "
                        f"({', '.join(entry['chosen_sources'])}, {choice.rule}) over {others}.",
                        (name,),
                    )
                )
    for name, value in spec.finalize(fields).items():
        provenance[name]["rule"] += f" (written as {fields[name]})"
        fields[name] = value
    if entity_id is None:
        ents = Counter(c.entity for c in cluster if c.entity is not None)
        entity_id = ents.most_common(1)[0][0] if ents else None
    view = RecordView(values=fields, meta=meta, method="reconciled")
    verdict = validate_record(info, view, settings)
    findings = [f for f in verdict.findings if f.rule != "escalated"] + findings
    findings += spec.cluster_findings(cluster)
    severities = {f.severity for f in findings}
    status: Status = (
        "rejected"
        if "reject" in severities
        else "needs_review"
        if "review" in severities
        else "auto_approved"
    )
    return fields, provenance, conflicts, status, findings, entity_id


def _same_entity(
    alt: dict[str, Any], by_id: dict[int, Candidate], winner: Candidate | None
) -> bool:
    """Spelling variants of the chosen organization aren't a conflict."""
    if winner is None:
        return False
    return all(by_id[i].entity == winner.entity for i in alt["ids"])


def reconcile_pack(
    session: Session, pack: str, settings: Settings, client: LLMClient | None = None
) -> ReconcileStats:
    info = get_pack(pack)
    spec = info.reconcile
    if spec is None:
        raise ValueError(f"pack {pack!r} has no reconciliation spec")
    stats = ReconcileStats()
    cands = load_candidates(session, info, settings)
    stats.candidates = len(cands)

    # 1. entities
    groups = name_groups(spec, cands)
    adjudicate = Adjudicator(session, info, settings, client)
    clusters, verdicts = resolve(groups, settings.match_auto, settings.match_review, adjudicate)
    stats.matches.update(adjudicate.counts)
    stats.llm_cost = float(adjudicate.stats.cost_usd)
    session.execute(delete(GoldenRecord).where(GoldenRecord.pack == pack))
    session.execute(delete(Entity).where(Entity.pack == pack))
    norm_to_entity: dict[str, int] = {}
    entity_names: dict[int, str] = {}
    pending_norms = {n for v in verdicts if v.decision == "gray" for n in (v.a, v.b)}
    for ec in clusters:
        aliases = ec.aliases()
        ent = Entity(
            pack=pack,
            kind=spec.entity_kind,
            canonical_name=ec.canonical,
            normalized=ec.groups[0].norm,
            aliases=[{"name": a, "count": n} for a, n in aliases.most_common()],
        )
        session.add(ent)
        session.flush()
        entity_names[ent.id] = ec.canonical
        for g in ec.groups:
            norm_to_entity[g.norm] = ent.id
        stats.entities += 1
        stats.aliases += len(aliases)

    pending_entities = {norm_to_entity[n] for n in pending_norms if n in norm_to_entity}
    for c in cands:
        name = c.values.get(spec.entity_field)
        c.entity = norm_to_entity.get(normalize(" ".join(str(name).split()))) if name else None

    # 2. records
    linking = link_records(spec, cands)
    reasons = linking.reasons
    for cluster in linking.clusters:
        fields, provenance, conflicts, status, findings, entity_id = build_golden(
            info, cluster, entity_names, settings
        )
        if any(c.id in linking.ambiguous for c in cluster):
            findings.append(
                Finding(
                    "ambiguous_match",
                    "review",
                    "Could belong to more than one record with the same name (namesakes); "
                    "kept separate.",
                    (),
                )
            )
            if status == "auto_approved":
                status = "needs_review"
        if entity_id in pending_entities:
            findings.append(
                Finding(
                    "entity_match_pending",
                    "review",
                    f"Whether '{entity_names[entity_id]}' is the same "
                    f"{spec.entity_kind} as a similar name is still undecided.",
                    (spec.entity_field,),
                )
            )
            if status == "auto_approved":
                status = "needs_review"
        golden = GoldenRecord(
            pack=pack,
            entity_id=entity_id,
            fields=fields,
            provenance=provenance,
            conflicts=conflicts,
            status=status,
            findings=[f.as_dict() for f in findings],
            source_count=len({c.source for c in cluster}),
            links=[RecordLink(extracted_record_id=c.id, reason=reasons[c.id]) for c in cluster],
        )
        session.add(golden)
        stats.golden += 1
        stats.multi_source += golden.source_count > 1
        stats.by_status[status] += 1
        stats.links.update(reasons[c.id] for c in cluster)
        stats.conflicts.update(c["field"] for c in conflicts)
        stats.by_rule.update({f.rule for f in findings if f.severity == "review"})
    session.commit()
    return stats
