from __future__ import annotations


ACTIVE_DATABASE_KEY = "active_database_path"


def normalize_database_path(value: object) -> str:
    path = str(value or "").strip()
    if len(path) >= 2 and path[0] == path[-1] and path[0] in ('"', "'"):
        path = path[1:-1].strip()
    return path


def database_path_input(default_path: str, widget_key: str) -> str:
    """Render a page-local input backed by permanent cross-page session state."""
    import streamlit as st

    if ACTIVE_DATABASE_KEY not in st.session_state:
        st.session_state[ACTIVE_DATABASE_KEY] = normalize_database_path(default_path)

    # Streamlit removes widget-owned keys when their page is no longer rendered.
    # Keep the durable value in a non-widget key and hydrate each page's widget.
    st.session_state[widget_key] = normalize_database_path(
        st.session_state[ACTIVE_DATABASE_KEY]
    )

    def commit_path() -> None:
        st.session_state[ACTIVE_DATABASE_KEY] = normalize_database_path(
            st.session_state.get(widget_key, default_path)
        )

    value = st.text_input(
        "SQLite database",
        key=widget_key,
        on_change=commit_path,
        help="This selection is shared by every dashboard page.",
        disabled=True,
    )
    normalized = normalize_database_path(value)
    st.session_state[ACTIVE_DATABASE_KEY] = normalized
    return normalized
