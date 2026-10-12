"""Review app entry point: `uv run streamlit run app/streamlit_app.py` (D-041)."""

import streamlit as st

st.set_page_config(page_title="Messy data review", layout="wide")
page = st.navigation(
    [
        st.Page("views/overview.py", title="Overview", default=True),
        st.Page("views/review.py", title="Review queue"),
        st.Page("views/exceptions.py", title="Exceptions"),
    ]
)
page.run()
