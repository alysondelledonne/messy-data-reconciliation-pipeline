"""Streamlit helpers shared by the app's pages: database session, sidebar, rendering
of source previews and tables. Page files in `app/` stay thin."""

import csv
import html
import io
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

import streamlit as st
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from pipeline.config import Settings, get_settings
from pipeline.review.preview import Preview
from pipeline.store.db import make_engine


@st.cache_resource
def _engine() -> Engine:
    return make_engine()


def _default_session() -> Session:
    return Session(_engine(), expire_on_commit=False)


# Tests replace this to run pages against their own session.
SESSION_FACTORY: Callable[[], Session] = _default_session


@contextmanager
def session() -> Iterator[Session]:
    s = SESSION_FACTORY()
    try:
        yield s
    finally:
        s.close()


def settings() -> Settings:
    return get_settings()


def sidebar(packs: list[str]) -> tuple[str | None, str]:
    """Pack picker and reviewer name; both persist across pages."""
    st.sidebar.header("Review settings")
    pack = st.sidebar.selectbox("Domain pack", packs, key="pack") if packs else None
    reviewer = st.sidebar.text_input(
        "Your name (recorded with every decision)", key="reviewer", placeholder="e.g. Sam"
    )
    if not packs:
        st.sidebar.info("No data yet: ingest, extract, validate and reconcile a pack first.")
    return pack, reviewer.strip()


def table(rows: list[dict[str, Any]], empty: str = "Nothing here.", **kw: Any) -> None:
    if rows:
        st.dataframe(rows, hide_index=True, width="stretch", **kw)
    else:
        st.caption(empty)


def csv_bytes(rows: list[dict[str, Any]]) -> bytes:
    if not rows:
        return b""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue().encode()


def _lines_html(lines: list[tuple[int, str, bool]]) -> str:
    body = []
    for n, text, hit in lines:
        line = f"<span style='opacity:.5'>{n:>4}</span>  {html.escape(text)}"
        body.append(f"<mark>{line}</mark>" if hit else line)
    return (
        "<pre style='white-space:pre-wrap;font-size:0.8rem;max-height:32rem;overflow:auto'>"
        + "\n".join(body)
        + "</pre>"
    )


def show_preview(p: Preview) -> None:
    st.caption(p.title)
    if p.note:
        st.warning(p.note)
    if p.kind == "pages":
        for i, png in enumerate(p.pages, 1):
            st.image(png, caption=f"page {i}", width="stretch")
        if p.lines:
            with st.expander("Text layer (evidence highlighted)"):
                st.markdown(_lines_html(p.lines), unsafe_allow_html=True)
    elif p.kind == "email":
        st.markdown(
            "\n".join(f"**{html.escape(k)}:** {html.escape(v)}  " for k, v in p.headers.items())
        )
        st.markdown(_lines_html(p.lines), unsafe_allow_html=True)
    elif p.kind == "table":
        rows = [
            {"": "▶" if i == p.highlight_row else "", **dict(zip(p.columns, r, strict=False))}
            for i, r in enumerate(p.rows)
        ]
        table(rows)
    else:
        st.markdown(_lines_html(p.lines), unsafe_allow_html=True)
