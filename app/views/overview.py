"""Overview: record counts by pack, source format and status, plus model cost."""

from collections import defaultdict

import streamlit as st

from pipeline.review import queries, ui

st.title("Overview")
with ui.session() as s:
    packs = queries.packs(s)
    ui.sidebar(packs)
    data = queries.overview(s)

golden = data["golden"]
total = sum(r["records"] for r in golden)
by_status: dict[str, int] = defaultdict(int)
for r in golden:
    by_status[r["status"]] += r["records"]
cost = sum(r["cost_usd"] for r in data["cost"])
cols = st.columns(5)
cols[0].metric("Golden records", total)
cols[1].metric("Auto-approved", by_status["auto_approved"])
cols[2].metric("Needs review", by_status["needs_review"])
cols[3].metric("Approved by a person", by_status["approved"])
cols[4].metric("Model cost", f"${cost:,.2f}")

st.subheader("Golden records by pack and status")
pivot: dict[str, dict[str, int]] = defaultdict(dict)
for r in golden:
    pivot[r["pack"]][r["status"]] = r["records"]
statuses = sorted({r["status"] for r in golden})
ui.table(
    [
        {"pack": p, **{st_: row.get(st_, 0) for st_ in statuses}, "total": sum(row.values())}
        for p, row in pivot.items()
    ],
    "No golden records yet: run reconcile.",
)

st.subheader("Source records by format and validation status")
src: dict[tuple[str, str], dict[str, int]] = defaultdict(dict)
for r in data["sources"]:
    src[(r["pack"], r["format"])][r["status"]] = r["records"]
vstatuses = sorted({r["status"] for r in data["sources"]})
ui.table(
    [
        {"pack": p, "format": f, **{v: row.get(v, 0) for v in vstatuses}}
        for (p, f), row in src.items()
    ],
    "Nothing validated yet.",
)

left, right = st.columns(2)
with left:
    st.subheader("Files ingested")
    ui.table(data["files"], "Nothing ingested yet.")
with right:
    st.subheader("Model calls and cost")
    ui.table(data["cost"], "No model calls yet.")
