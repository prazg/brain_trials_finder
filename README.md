# Brain Cancer Trials Finder (Streamlit)

A minimal Streamlit app that discovers actively recruiting neuro-oncology clinical trials (e.g., glioblastoma, brain tumors) from the ClinicalTrials.gov v2 API and ranks them with simple, explainable heuristics.

Demo features:
- Filters by country and basic patient factors (age, KPS, setting).
- Pulls live data from ClinicalTrials.gov v2.
- Scores trials with transparent reasons for the score.

## Local run

Prereqs: Python 3.9+.

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
streamlit run streamlit_app.py
```

## Deploy to Streamlit Community Cloud

1) Push this folder to a new GitHub repository (see steps below).
2) Go to https://share.streamlit.io/ and connect your GitHub account.
3) Create a new app, select your repo, branch (e.g., `main`), and set the main file path to `streamlit_app.py`.
4) Click Deploy. Streamlit will build from `requirements.txt` automatically.

## GitHub repository setup (Windows, cmd.exe)

1) Create an empty repo on GitHub (e.g., `brain-trials-finder`). Do not add any files there.
2) In this project folder, run:

```bat
git init
git add .
git commit -m "Initial commit: Streamlit Brain Cancer Trials Finder"
git branch -M main
REM Replace <YOUR-USERNAME> and repo name below
git remote add origin https://github.com/<YOUR-USERNAME>/brain-trials-finder.git
git push -u origin main
```

If you prefer SSH:

```bat
git remote remove origin 2> NUL
git remote add origin git@github.com:<YOUR-USERNAME>/brain-trials-finder.git
git push -u origin main
```

## Configuration

- No secrets are required. All data is pulled from the public ClinicalTrials.gov API.
- If you want to pin Python version on Streamlit Cloud, add a `runtime.txt` (e.g., `python-3.10`), though it’s optional.

## Files

- `streamlit_app.py` — Streamlit entrypoint used by Streamlit Cloud.
- `GUI_CLinicalTrial.py` — Your original app file; kept for development, but `streamlit_app.py` mirrors it for deployment.
- `requirements.txt` — Python dependencies.
- `.gitignore` — Ignores typical Python/venv artifacts.

## Disclaimer

This tool provides assistive information only and is not a substitute for professional medical advice. Always discuss clinical trials with your clinician.

