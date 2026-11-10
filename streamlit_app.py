# Streamlit entrypoint:  streamlit run streamlit_app.py
#
# The app itself lives in GUI_CLinicalTrial.py. It is executed with runpy
# rather than imported: Streamlit re-runs this file on every interaction, and a
# plain `import` only executes a module the first time in a process, so every
# rerun (and every visitor after the first) would get a blank page.
import os
import runpy

try:
    runpy.run_path(os.path.join(os.path.dirname(os.path.abspath(__file__)), "GUI_CLinicalTrial.py"),
                   run_name="__main__")
except Exception as exc:  # Streamlit's own control-flow exceptions are BaseException-based or re-raised below
    if exc.__class__.__name__ in ("StopException", "RerunException"):
        raise
    import streamlit as st

    st.error("The app could not start. Check that GUI_CLinicalTrial.py and ctgov_client.py are present.")
    st.exception(exc)
