"""Run extraction for a pack: load pending raw records, call the model (with caching,
concurrency, retries and escalation), and store the results."""

import json
from collections import Counter, defaultdict
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from pipeline.config import Settings
from pipeline.domains import PackInfo, get_pack
from pipeline.extract.client import LLMClient, LLMRequest, LLMResponse, cost_usd
from pipeline.extract.core import (
    SKIP_KINDS,
    STRUCTURED_KINDS,
    Extracted,
    FieldMapping,
    apply_mapping,
    escalation_reasons,
    interpret_document,
    parse_mapping,
    row_columns,
    signature,
)
from pipeline.extract.prompts import (
    PROMPT_VERSION,
    document_content,
    document_system,
    mapping_content,
    mapping_system,
)
from pipeline.extract.schema import (
    extraction_schema,
    mapping_schema,
    required_fields,
    schema_hash,
)
from pipeline.store.models import (
    ColumnMapping,
    ExtractedRecord,
    ExtractionResult,
    IngestRun,
    LLMCall,
    RawRecordRow,
    SourceFile,
)


def extractor_version(pack: PackInfo) -> str:
    return f"{PROMPT_VERSION}-{schema_hash(extraction_schema(pack.schema))[:8]}"


@dataclass
class CallOutcome:
    response: LLMResponse
    call_id: int
    cached: bool


@dataclass
class RunStats:
    raw_records: int = 0
    by_status: Counter[str] = field(default_factory=Counter)
    by_method: Counter[str] = field(default_factory=Counter)
    records_extracted: int = 0
    escalated: int = 0
    api_calls: int = 0
    cache_hits: int = 0
    cost_usd: Decimal = Decimal(0)
    mappings_created: int = 0

    def summary(self) -> str:
        return (
            f"raw records: {self.raw_records} | results: {dict(self.by_status)} | "
            f"methods: {dict(self.by_method)}\n"
            f"extracted records: {self.records_extracted} | escalated: {self.escalated} | "
            f"new mappings: {self.mappings_created}\n"
            f"API calls: {self.api_calls} (cache hits: {self.cache_hits}) | "
            f"cost this run: ${self.cost_usd:.4f}"
        )


class CachedCaller:
    """Answer requests from `llm_calls` when possible; call the model concurrently otherwise.

    Only successful (status ok) responses are reusable. Failures are logged with
    a null cache key, so a retry really calls the model again.
    """

    def __init__(
        self, session: Session, client: LLMClient, pack: str, settings: Settings, stats: RunStats
    ) -> None:
        self.session, self.client, self.pack = session, client, pack
        self.settings, self.stats = settings, stats

    def run(self, requests: Sequence[LLMRequest]) -> list[CallOutcome]:
        keys = [r.cache_key(PROMPT_VERSION) for r in requests]
        hits = {
            row.cache_key: row
            for row in self.session.scalars(
                select(LLMCall).where(LLMCall.cache_key.in_(set(keys)), LLMCall.status == "ok")
            )
        }
        todo: dict[str, LLMRequest] = {}
        for key, req in zip(keys, requests, strict=True):
            if key not in hits and key not in todo:
                todo[key] = req
        fresh: dict[str, CallOutcome] = {}
        if todo:
            with ThreadPoolExecutor(max_workers=self.settings.extract_workers) as pool:
                responses = list(pool.map(self.client.complete, todo.values()))
            for (key, req), resp in zip(todo.items(), responses, strict=True):
                row = self._log(key, req, resp)
                fresh[key] = CallOutcome(resp, row.id, cached=False)
            self.session.commit()
        out: list[CallOutcome] = []
        for key in keys:
            if key in fresh:
                out.append(fresh[key])
            else:
                row = hits[key]
                self.stats.cache_hits += 1
                out.append(
                    CallOutcome(
                        LLMResponse("ok", row.response, row.model_served, row.stop_reason),
                        row.id,
                        cached=True,
                    )
                )
        return out

    def _log(self, key: str, req: LLMRequest, resp: LLMResponse) -> LLMCall:
        cost = cost_usd(resp, self.settings.prices)
        self.stats.api_calls += 1
        self.stats.cost_usd += cost
        row = LLMCall(
            cache_key=key if resp.status == "ok" else None,
            pack=self.pack,
            purpose=req.purpose,
            prompt_version=PROMPT_VERSION,
            model_requested=req.model,
            model_served=resp.model_served,
            status=resp.status,
            stop_reason=resp.stop_reason,
            input_tokens=resp.input_tokens,
            output_tokens=resp.output_tokens,
            cache_read_tokens=resp.cache_read_tokens,
            cache_write_tokens=resp.cache_write_tokens,
            cost_usd=cost,
            latency_ms=resp.latency_ms,
            response=resp.data,
            error=resp.error,
            raw_record_id=req.meta.get("raw_record_id"),
        )
        self.session.add(row)
        self.session.flush()
        return row


def _call_with_escalation(
    caller: CachedCaller,
    requests: list[LLMRequest],
    judge: Callable[[int, LLMResponse], list[str]],
    make_big: Callable[[LLMRequest], LLMRequest],
) -> list[tuple[LLMResponse | None, list[int], list[str], bool]]:
    """Small model, one retry for broken output, then the escalation model.

    `judge(i, response)` returns reasons the response for request i isn't good
    enough; an empty list keeps it. Returns per request: (final response or None,
    call ids, reasons, escalated).
    """
    n = len(requests)
    calls: list[list[int]] = [[] for _ in range(n)]
    final: list[LLMResponse | None] = [None] * n
    reasons: list[list[str]] = [[] for _ in range(n)]
    first = caller.run(requests)
    retry = [
        i for i, o in enumerate(first) if o.response.status in ("invalid", "max_tokens", "error")
    ]
    outcomes = dict(enumerate(first))
    for i in range(n):
        calls[i].append(first[i].call_id)
    if retry:
        for i, o in zip(retry, caller.run([requests[i] for i in retry]), strict=True):
            outcomes[i] = o
            calls[i].append(o.call_id)
    escalate: list[int] = []
    for i in range(n):
        resp = outcomes[i].response
        if resp.status != "ok":
            reasons[i] = [f"{requests[i].model}: {resp.status} ({resp.error or resp.stop_reason})"]
            escalate.append(i)
            continue
        final[i] = resp
        reasons[i] = judge(i, resp)
        if reasons[i]:
            escalate.append(i)
    escalated = [False] * n
    if escalate:
        big = caller.run([make_big(requests[i]) for i in escalate])
        for i, o in zip(escalate, big, strict=True):
            calls[i].append(o.call_id)
            escalated[i] = True
            if o.response.status == "ok":
                final[i] = o.response
            else:
                reasons[i].append(f"escalation failed: {o.response.status}")
    return [(final[i], calls[i], reasons[i], escalated[i]) for i in range(n)]


def _source_path(sf: SourceFile, run: IngestRun, files_root: Path | None) -> Path | None:
    if files_root is not None:
        return files_root / sf.path
    if root := sf.meta.get("root"):
        return Path(root) / sf.path
    dirs = [Path(p) for p in run.inputs if Path(p).is_dir()]
    return dirs[0] / sf.path if len(dirs) == 1 else None


def _file_context(sf: SourceFile, r: RawRecordRow) -> list[str]:
    """Report titles and sheet names that can state facts about every row."""
    out: list[str] = []
    preamble = sf.meta.get("preamble")
    sheet = r.location.get("sheet")
    if isinstance(preamble, dict) and sheet:
        out += list(preamble.get(sheet, []))
    elif isinstance(preamble, list):
        out += preamble
    if sheet:
        out.append(f"sheet: {sheet}")
    return out


def pending_records(
    session: Session, pack: str, version: str
) -> list[tuple[RawRecordRow, SourceFile]]:
    """Raw records of the latest version of each file that this extractor hasn't finished:
    never seen, or failed last time (a failed call isn't cached, so it is retried)."""
    latest = (
        select(func.max(SourceFile.id))
        .where(SourceFile.pack == pack, SourceFile.status == "ok")
        .group_by(SourceFile.path)
    )
    done = select(ExtractionResult.raw_record_id).where(
        ExtractionResult.extractor_version == version, ExtractionResult.status != "failed"
    )
    q = (
        select(RawRecordRow, SourceFile)
        .join(SourceFile)
        .where(SourceFile.id.in_(latest), RawRecordRow.id.not_in(done))
        .order_by(RawRecordRow.id)
    )
    return [(r, sf) for r, sf in session.execute(q).all()]


class Extractor:
    def __init__(
        self,
        session: Session,
        client: LLMClient,
        pack: str,
        settings: Settings,
        files_root: Path | None = None,
        log: Callable[[str], None] = print,
    ) -> None:
        self.session, self.settings, self.log = session, settings, log
        self.pack = get_pack(pack)
        self.model: type[BaseModel] = self.pack.schema
        self.version = extractor_version(self.pack)
        self.stats = RunStats()
        self.caller = CachedCaller(session, client, pack, settings, self.stats)
        self.files_root = files_root
        self.doc_schema = extraction_schema(self.model)
        self.doc_system = document_system(self.pack)

    # -- public ------------------------------------------------------------------------------

    def run(self, limit: int | None = None, chunk: int = 40) -> RunStats:
        pending = pending_records(self.session, self.pack.name, self.version)
        skip = [(r, sf) for r, sf in pending if r.kind in SKIP_KINDS]
        rows = [(r, sf) for r, sf in pending if r.kind in STRUCTURED_KINDS]
        docs = [(r, sf) for r, sf in pending if r.kind not in SKIP_KINDS | STRUCTURED_KINDS]
        if limit is not None:  # --limit caps model calls on documents; mapped rows are cheap
            docs = docs[:limit]
        self.stats.raw_records = len(skip) + len(rows) + len(docs)
        for r, _ in skip:
            self._save(r, "skipped", "skipped", None, [], [], [f"kind {r.kind} holds no record"])
        self.session.commit()
        if rows:
            self.log(f"structured rows: {len(rows)}")
            self._run_rows(rows)
        for start in range(0, len(docs), chunk):
            batch = docs[start : start + chunk]
            self.log(f"documents {start + 1}-{start + len(batch)} of {len(docs)}")
            self._run_docs(batch)
        return self.stats

    # -- structured rows via column mappings -------------------------------------------------

    def _run_rows(self, rows: list[tuple[RawRecordRow, SourceFile]]) -> None:
        groups: dict[str, list[tuple[RawRecordRow, dict[str, Any]]]] = defaultdict(list)
        columns_of: dict[str, list[str]] = {}
        context_of: dict[str, list[str]] = {}
        for r, sf in rows:
            cols = row_columns(r.kind, r.raw)
            sig = signature(self.pack.name, list(cols))
            groups[sig].append((r, cols))
            columns_of[sig] = list(cols)
            if sig not in context_of:
                context_of[sig] = _file_context(sf, r)
        mappings = self._mappings(
            {sig: (columns_of[sig], g) for sig, g in groups.items()}, context_of
        )
        for sig, members in groups.items():
            got = mappings.get(sig)
            for r, cols in members:
                if got is None:
                    self._save(r, "column_mapping", "failed", None, [], [], ["no usable mapping"])
                    continue
                cm, fms = got
                rec = apply_mapping(self.model, fms, cols, r.normalized, list(r.flags))
                status = "ok" if rec.has_values() else "empty"
                self._save(
                    r,
                    "column_mapping",
                    status,
                    cm.mapping.get("model"),
                    [rec] if rec.has_values() else [],
                    [],
                    [],
                    mapping_id=cm.id,
                )
        self.session.commit()

    def _mappings(
        self,
        groups: dict[str, tuple[list[str], list[tuple[RawRecordRow, dict[str, Any]]]]],
        context_of: dict[str, list[str]],
    ) -> dict[str, tuple[ColumnMapping, list[FieldMapping]]]:
        out: dict[str, tuple[ColumnMapping, list[FieldMapping]]] = {}
        existing = {
            cm.signature: cm
            for cm in self.session.scalars(
                select(ColumnMapping).where(
                    ColumnMapping.pack == self.pack.name,
                    ColumnMapping.prompt_version == PROMPT_VERSION,
                    ColumnMapping.signature.in_(list(groups)),
                )
            )
        }
        need: list[str] = []
        requests: list[LLMRequest] = []
        for sig, (cols, members) in groups.items():
            if sig in existing:
                cm = existing[sig]
                out[sig] = (cm, parse_mapping(self.model, cm.mapping, cols))
                continue
            need.append(sig)
            requests.append(
                self._mapping_request(cols, [c for _, c in members], context_of.get(sig))
            )
        if not requests:
            return out
        required = required_fields(self.model)

        def judge(i: int, resp: LLMResponse) -> list[str]:
            fms = {
                m.field: m for m in parse_mapping(self.model, resp.data or {}, groups[need[i]][0])
            }
            bad = []
            for name in required:
                m = fms.get(name)
                if m is not None and m.column and m.confidence < self.settings.confidence_threshold:
                    bad.append(f"low-confidence mapping for {name}")
            return bad

        results = _call_with_escalation(self.caller, requests, judge, self._big)
        for sig, (resp, call_ids, reasons, _escalated) in zip(need, results, strict=True):
            if resp is None:
                self.log(f"  mapping failed for columns {groups[sig][0]}: {reasons}")
                continue
            data = dict(resp.data or {})
            data["model"] = resp.model_served
            data["reasons"] = reasons
            cm = ColumnMapping(
                pack=self.pack.name,
                signature=sig,
                prompt_version=PROMPT_VERSION,
                columns=groups[sig][0],
                mapping=data,
                llm_call_id=call_ids[-1],
            )
            self.session.add(cm)
            self.session.flush()
            self.stats.mappings_created += 1
            out[sig] = (cm, parse_mapping(self.model, data, groups[sig][0]))
        self.session.commit()
        return out

    def _mapping_request(
        self, columns: list[str], rows: list[dict[str, Any]], context: list[str] | None = None
    ) -> LLMRequest:
        samples = [{c: r.get(c) for c in columns} for r in rows[:8]]
        distinct: dict[str, list[str]] = {}
        for c in columns:
            values = {str(r.get(c)) for r in rows if r.get(c) not in (None, "")}
            if 0 < len(values) <= 12:
                distinct[c] = sorted(values)
        return LLMRequest(
            model=self.settings.extract_model,
            system=mapping_system(self.pack),
            content=mapping_content(self.model, columns, samples, distinct, context),
            schema=mapping_schema(self.model, columns),
            effort=self.settings.extract_effort,
            max_tokens=self.settings.extract_max_tokens,
            purpose="column_mapping",
            meta={"columns": columns},
        )

    # -- documents --------------------------------------------------------------------------

    def _run_docs(self, docs: list[tuple[RawRecordRow, SourceFile]]) -> None:
        requests: list[LLMRequest] = []
        sources: list[str | None] = []  # text for evidence checks; None for scans
        kept: list[RawRecordRow] = []
        for r, sf in docs:
            pdf_bytes = None
            if "needs_vision" in r.flags:
                path = _source_path(sf, sf.run, self.files_root)
                if path is None or not path.exists():
                    self._save(
                        r,
                        "llm",
                        "failed",
                        None,
                        [],
                        [],
                        [f"source file not found for vision: {sf.path} (use --files-root)"],
                    )
                    continue
                pdf_bytes = path.read_bytes()
            requests.append(
                LLMRequest(
                    model=self.settings.extract_model,
                    system=self.doc_system,
                    content=document_content(sf.path, r.kind, r.location, r.text, pdf_bytes),
                    schema=self.doc_schema,
                    effort=self.settings.extract_effort,
                    max_tokens=self.settings.extract_max_tokens,
                    meta={
                        "raw_record_id": r.id,
                        "source": sf.path,
                        "location": r.location,
                        "kind": r.kind,
                    },
                )
            )
            sources.append(None if pdf_bytes is not None else r.text)
            kept.append(r)

        parsed: dict[tuple[int, str], list[Extracted]] = {}

        def interpret(i: int, resp: LLMResponse) -> list[Extracted]:
            key = (i, resp.model_served)
            if key not in parsed:
                parsed[key] = interpret_document(self.model, resp.data or {}, sources[i])
            return parsed[key]

        def judge(i: int, resp: LLMResponse) -> list[str]:
            return escalation_reasons(self.model, interpret(i, resp), self.settings)

        results = _call_with_escalation(self.caller, requests, judge, self._big)
        for i, (r, (resp, call_ids, reasons, escalated)) in enumerate(
            zip(kept, results, strict=True)
        ):
            if resp is None:
                self._save(r, "llm", "failed", None, [], call_ids, reasons, escalated=escalated)
                continue
            recs = [x for x in interpret(i, resp) if x.has_values()]
            status = "ok" if recs else "empty"
            self._save(
                r, "llm", status, resp.model_served, recs, call_ids, reasons, escalated=escalated
            )
        self.session.commit()

    def _big(self, req: LLMRequest) -> LLMRequest:
        return LLMRequest(
            model=self.settings.escalation_model,
            system=req.system,
            content=req.content,
            schema=req.schema,
            effort=self.settings.escalation_effort,
            max_tokens=req.max_tokens,
            purpose=req.purpose,
            meta=req.meta,
        )

    # -- persistence --------------------------------------------------------------------------

    def _save(
        self,
        r: RawRecordRow,
        method: str,
        status: str,
        model: str | None,
        records: list[Extracted],
        call_ids: list[int],
        reasons: list[str],
        *,
        escalated: bool = False,
        mapping_id: int | None = None,
    ) -> None:
        self.stats.by_status[status] += 1
        self.stats.by_method[method] += 1
        self.stats.records_extracted += len(records)
        self.stats.escalated += escalated
        self.session.execute(  # a retry replaces the earlier failed result
            delete(ExtractionResult).where(
                ExtractionResult.raw_record_id == r.id,
                ExtractionResult.extractor_version == self.version,
                ExtractionResult.status == "failed",
            )
        )
        self.session.add(
            ExtractionResult(
                raw_record_id=r.id,
                pack=self.pack.name,
                extractor_version=self.version,
                method=method,
                status=status,
                model=model,
                escalated=escalated,
                reasons=reasons,
                llm_call_ids=call_ids,
                column_mapping_id=mapping_id,
                records=[
                    ExtractedRecord(
                        record_index=i, fields=rec.values(), field_meta=rec.meta(), flags=rec.flags
                    )
                    for i, rec in enumerate(records)
                ],
            )
        )


# --- estimates (no API calls) -------------------------------------------------------------


def estimate(session: Session, pack: str, settings: Settings, limit: int | None = None) -> str:
    """Rough call count and cost range for the pending work, without calling the model."""
    info = get_pack(pack)
    version = extractor_version(info)
    pending = pending_records(session, pack, version)
    docs = [(r, sf) for r, sf in pending if r.kind not in SKIP_KINDS | STRUCTURED_KINDS]
    rows = [r for r, _ in pending if r.kind in STRUCTURED_KINDS]
    if limit is not None:
        docs = docs[:limit]
    sigs = {signature(pack, list(row_columns(r.kind, r.raw))) for r in rows}
    known = set(
        session.scalars(
            select(ColumnMapping.signature).where(
                ColumnMapping.pack == pack, ColumnMapping.prompt_version == PROMPT_VERSION
            )
        )
    )
    new_maps = len(sigs - known)
    schema_tokens = len(json.dumps(extraction_schema(info.schema))) // 4
    system_tokens = len(document_system(info)) // 4
    in_tok = 0
    for r, sf in docs:
        pages = int(sf.meta.get("pages", 1)) if "needs_vision" in r.flags else 0
        in_tok += system_tokens + schema_tokens + (pages * 1600 if pages else len(r.text) // 3)
    in_tok += new_maps * 2500
    calls = len(docs) + new_maps
    out_low, out_high = calls * 400, calls * 2500  # thinking makes output the wide range
    small = settings.prices.get(settings.extract_model)
    big = settings.prices.get(settings.escalation_model)
    lines = [
        f"pack {pack}: {len(pending)} pending raw records -> {len(docs)} documents, "
        f"{len(rows)} structured rows in {len(sigs)} layouts ({new_maps} need a new mapping)",
        f"estimated small-model calls: {calls}; input ~{in_tok:,} tokens; "
        f"output ~{out_low:,}-{out_high:,} tokens",
    ]
    if small and big:
        lo = (in_tok * small.input + out_low * small.output) / 1e6
        hi = (in_tok * small.input + out_high * small.output) / 1e6
        esc_lo = 0.05 * (in_tok * big.input + out_low * big.output) / 1e6
        esc_hi = 0.25 * (in_tok * big.input + out_high * big.output) / 1e6
        lines.append(
            f"estimated cost: {settings.extract_model} ${lo:.2f}-${hi:.2f} + escalations to "
            f"{settings.escalation_model} (5-25% of calls) ${esc_lo:.2f}-${esc_hi:.2f}"
        )
    lines.append("Rough guide only; real token counts are logged in llm_calls.")
    return "\n".join(lines)
