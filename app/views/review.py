"""Review queue: the source on the left, the golden record and its reasons on the right;
approve or correct. Plus close name matches waiting for a person."""

import json
from typing import Any

import streamlit as st

from pipeline.domains import get_pack
from pipeline.reconcile.engine import reconcile_pack
from pipeline.review import queries, service, ui
from pipeline.review.preview import build_preview, evidence_of
from pipeline.store.models import GoldenRecord

st.title("Review queue")
STATUSES = ["needs_review", "auto_approved", "approved", "rejected"]


def _show(v: Any) -> str:
    if isinstance(v, list):
        return f"{len(v)} items"
    return "" if v is None else str(v)


def records_tab(s: Any, pack: str, reviewer: str) -> None:
    info = get_pack(pack)
    f1, f2 = st.columns(2)
    statuses = f1.multiselect("Status", STATUSES, default=["needs_review"], key="q_status")
    rules = sorted(
        {
            r
            for row in queries.review_queue(s, pack, tuple(STATUSES))
            for r in row["reasons"].split(", ")
            if r
        }
    )
    rule = f2.selectbox("Reason", ["(any)", *rules], key="q_rule")
    queue = queries.review_queue(s, pack, tuple(statuses), None if rule == "(any)" else rule)
    st.caption(f"{len(queue)} records")
    if not queue:
        st.success("Nothing to review with these filters.")
        return
    options = {
        r["id"]: f"#{r['id']} · {r['record']} · {r['reasons'] or r['status']}" for r in queue
    }
    golden_id = st.selectbox(
        "Record", list(options), format_func=options.__getitem__, key="q_record"
    )
    g = s.get(GoldenRecord, golden_id)
    if g is None:
        st.warning("That record is gone: reconcile ran again. Pick another.")
        return
    sources = queries.golden_sources(s, g.id)
    left, right = st.columns([1, 1], gap="large")

    with left:
        st.subheader("Source")
        names = [f"{sv.file.path} ({sv.link_reason})" for sv in sources]
        pick = st.radio(
            "Linked source records",
            range(len(sources)),
            format_func=names.__getitem__,
            key=f"src_{g.id}",
        )
        sv = sources[pick or 0]
        ui.show_preview(
            build_preview(s, sv.raw, sv.file, evidence_of(sv.field_meta), ui.settings().files_root)
        )
        with st.expander("What this source says"):
            ui.table(
                [
                    {
                        "field": k,
                        "value": _show(v),
                        "confidence": (sv.field_meta.get(k) or {}).get("final_confidence"),
                        "evidence": (sv.field_meta.get(k) or {}).get("evidence"),
                    }
                    for k, v in sv.fields.items()
                    if v is not None
                ]
            )
            for f in sv.validation:
                if f["severity"] != "info":
                    st.caption(f"⚠ {f['message']}")

    with right:
        st.subheader(f"Golden record · {g.status.replace('_', ' ')}")
        for f in g.findings:
            if f["severity"] == "reject":
                st.error(f["message"])
            elif f["severity"] == "review":
                st.warning(f["message"])
            else:
                st.caption(f"Note: {f['message']}")
        rows = []
        for name in info.schema.model_fields:
            prov = g.provenance.get(name) or {}
            rows.append(
                {
                    "field": name,
                    "value": _show(g.fields.get(name)),
                    "why": prov.get("rule", ""),
                    "confidence": prov.get("confidence"),
                    "other values": "; ".join(
                        f"{_show(a['value'])} ({', '.join(a['sources'])})"
                        for a in prov.get("alternatives", [])
                    ),
                }
            )
        ui.table(rows)
        items = g.fields.get("line_items")
        if isinstance(items, list) and items:
            with st.expander(f"Line items ({len(items)})"):
                ui.table(items)

        if not reviewer:
            st.info("Enter your name in the sidebar to approve or correct.")
            return
        actions(s, g, info, reviewer)


def actions(s: Any, g: Any, info: Any, reviewer: str) -> None:
    for f in g.findings:
        if (
            f["rule"] == "review_orphaned"
            and f.get("previous_key")
            and st.button("Re-apply the earlier corrections", key=f"reapply_{g.id}")
        ):
            n = service.reapply(s, g.id, f["previous_key"], reviewer, ui.settings())
            st.toast(f"Re-applied {n} correction(s).")
            st.rerun()
    note = st.text_input("Note (optional)", key=f"note_{g.id}")
    c1, _ = st.columns(2)
    if g.status != service.APPROVED:
        if c1.button("Approve", type="primary", key=f"approve_{g.id}"):
            service.approve(s, g.id, reviewer, note)
            st.toast("Approved.")
            st.rerun()
    elif c1.button("Undo approval", key=f"unapprove_{g.id}"):
        service.unapprove(s, g.id, reviewer, note)
        st.rerun()
    with st.form(key=f"correct_{g.id}"):
        st.markdown("**Correct a field**")
        field = st.selectbox("Field", list(info.schema.model_fields), key=f"field_{g.id}")
        current = g.fields.get(field)
        value = st.text_input(
            "New value (empty clears it; JSON for lists)",
            value=json.dumps(current) if isinstance(current, list) else _show(current),
        )
        if st.form_submit_button("Save correction"):
            try:
                findings = service.correct(s, g.id, field, value, reviewer, ui.settings(), note)
            except ValueError as e:
                st.error(f"Not saved: {e}")
            else:
                left = [f["message"] for f in findings if f["severity"] == "review"]
                st.toast("Saved." + (f" Still flagged: {len(left)}." if left else ""))
                st.rerun()


def matches_tab(s: Any, pack: str, reviewer: str) -> None:
    pending = queries.pending_matches(s, pack)
    st.caption(
        f"{len(pending)} pairs of names the rules and the model couldn't settle. A decision "
        "takes effect when reconcile runs again."
    )
    for i, m in enumerate(pending):
        with st.container(border=True):
            a, b = st.columns(2)
            a.markdown(f"**{m['a']}** · {m['a_mentions']} mentions")
            a.caption(", ".join(m["a_spellings"]) or "—")
            b.markdown(f"**{m['b']}** · {m['b_mentions']} mentions")
            b.caption(", ".join(m["b_spellings"]) or "—")
            st.caption(f"{m['decided_by']}: {m['reason']}")
            if reviewer:
                c1, c2, _ = st.columns([1, 1, 3])
                if c1.button("Same organization", key=f"same_{i}"):
                    service.decide_match(s, pack, m["a"], m["b"], True, reviewer)
                    st.rerun()
                if c2.button("Different", key=f"diff_{i}"):
                    service.decide_match(s, pack, m["a"], m["b"], False, reviewer)
                    st.rerun()
    if not reviewer:
        st.info("Enter your name in the sidebar to decide.")
    if st.button("Re-run reconcile now (no model calls)", key="rerun_reconcile"):
        with st.spinner("Reconciling…"):
            stats = reconcile_pack(s, pack, ui.settings(), client=None)
        st.code(stats.summary())


with ui.session() as s:
    pack, reviewer = ui.sidebar(queries.packs(s))
    if pack:
        records, matches = st.tabs(["Records", "Name matches"])
        with records:
            records_tab(s, pack, reviewer)
        with matches:
            matches_tab(s, pack, reviewer)
