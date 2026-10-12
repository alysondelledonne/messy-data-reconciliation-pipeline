"""Exceptions: duplicates, unmatched records, cross-source conflicts, rule failures, and
the audit trail of human decisions."""

import streamlit as st

from pipeline.review import queries, ui

st.title("Exceptions")

TABS = {
    "duplicates": ("Duplicates", "The same record arrived more than once."),
    "unmatched": ("Unmatched", "Records found in one place only, or missing their counterpart."),
    "conflicts": ("Conflicts", "Sources disagree on a field: what was chosen and what lost."),
    "rule_failures": ("Rule failures", "Golden records that fail a pack rule or a check."),
}

with ui.session() as s:
    pack, _ = ui.sidebar(queries.packs(s))
    if pack:
        data = queries.exceptions(s, pack)
        audit = queries.audit_log(s, pack)
        tabs = st.tabs([f"{TABS[k][0]} ({len(v)})" for k, v in data.items()] + ["Audit trail"])
        for tab, (key, rows) in zip(tabs, data.items(), strict=False):
            with tab:
                st.caption(TABS[key][1])
                rules = sorted({str(r.get("rule")) for r in rows})
                if len(rules) > 1:
                    pick = st.multiselect("Filter by rule", rules, key=f"filter_{key}")
                    rows = [r for r in rows if not pick or str(r.get("rule")) in pick]
                ui.table(rows)
                if rows:
                    st.download_button(
                        "Download CSV",
                        ui.csv_bytes(rows),
                        file_name=f"{pack}_{key}.csv",
                        key=f"dl_{key}",
                    )
        with tabs[-1]:
            st.caption("Every approval, correction and match decision, newest first.")
            ui.table(audit, "No human decisions yet.")
