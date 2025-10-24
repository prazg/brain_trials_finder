# Streamlit Cloud entrypoint. We reuse your existing app as-is.
# Main requirement: keep the module name and path exactly as below so Linux (Streamlit Cloud) can import it.

# IMPORTANT: Do not call st.set_page_config here to avoid double configuration.
# The original module handles all Streamlit layout and rendering.

try:
    import GUI_CLinicalTrial  # noqa: F401  # importing runs the Streamlit app defined at top-level
except Exception as e:
    import streamlit as st
    st.error("Failed to import GUI_CLinicalTrial.py. Ensure the file exists and has no syntax errors.")
    st.exception(e)
