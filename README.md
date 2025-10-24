# Brain Trials Finder

Fetch and rank actively recruiting neuro-oncology clinical trials from ClinicalTrials.gov (v2 API).
(Currently works well with Glioblastoma)

## Local run

Requirements: Python 3.9+ (works on 3.9–3.13), pip, internet access.

1) Create and activate a virtual environment (optional but recommended)

Windows (cmd):

```
python -m venv .venv
.venv\Scripts\activate
```

2) Install dependencies

```
pip install -r requirements.txt
```

3) Start Streamlit app

```
streamlit run streamlit_app.py
```

Then open the URL printed in the terminal (usually http://localhost:8501).

## Streamlit Cloud deploy

1) Push this folder to a GitHub repo (e.g. `brain_trials_finder`). Ensure these files are present at the repo root:
- `GUI_CLinicalTrial.py` (the app)
- `streamlit_app.py` (entrypoint that imports the app)
- `requirements.txt`

2) In Streamlit Cloud, create a new app:
- Repo: your `user/brain_trials_finder`
- Branch: main
- Main file: `streamlit_app.py`

3) (Optional) Set Python version to 3.9–3.13. The app is version-tolerant.

## Notes
- Results are limited to overallStatus RECRUITING and NOT_YET_RECRUITING via the v2 filter `filter.overallStatus`.
- Use the sidebar to change Diagnosis, Disease setting, Age, and filters. The app caches API responses for 1 hour; use the "Refresh data" button to clear cache.
- The app is robust to inconsistent API data and will skip malformed studies rather than failing.

## Troubleshooting
- If the app appears empty after a change, check the banner messages; you may have zero matches due to filters. Try clearing Country, widening Diagnosis, or clicking Refresh.
- Check logs on Streamlit Cloud via the "More" menu > "View logs". Any skipped records count is displayed at the bottom of results.


