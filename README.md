# Brain Cancer Trials Finder

![PRECISE-GBM Logo](logo_precise.png)

Finds actively recruiting neuro-oncology clinical trials — glioblastoma, glioma,
brain metastases, meningioma, medulloblastoma and others — across three public
registers, and ranks them against a patient's age, performance status and
disease setting with scoring you can read.

Three ways to use it:

| | What it is | Data sources |
|---|---|---|
| **Web page** | `docs/index.html`, published on GitHub Pages | ClinicalTrials.gov + ISRCTN live, EU CTIS from a daily snapshot |
| **Streamlit app** | `streamlit_app.py` | ClinicalTrials.gov |
| **Desktop app** | `Brain Cancer Trial Finder.exe`, built from `GUI_CLinicalTrial.py` | ClinicalTrials.gov, plus deep links to CRUK / NIHR / ISRCTN |

**This is a search aid, not medical advice.** Eligibility is decided by the trial
team. Always confirm with the treating clinician and the trial contact.

---

## The web page

Open `docs/index.html` locally or visit the published site. It loads with a
glioblastoma / United Kingdom search already run, so there is something on
screen immediately.

- **Diagnosis** picks a synonym set (Glioblastoma also searches GBM, glioblastoma
  multiforme, gliosarcoma) rather than a single string.
- **Region** filters by where the trial *sites* are, not where it is registered,
  so a US-sponsored trial recruiting in London appears under United Kingdom.
- **Patient factors** — age, KPS, newly diagnosed vs recurrent, prior
  bevacizumab — feed the match score.
- **Every score is itemised.** Each card lists what added points and what
  subtracted them ("Phase 2", "Maximum age 39", "Excludes prior bevacizumab"),
  so a score of 0 is explained rather than mysterious.
- Results export to CSV with the scoring reasons included.

### How the three registers are reached

This matters, because it is the constraint the whole design is built around.
GitHub Pages serves static files, so anything live has to happen in the
visitor's browser, and that only works where a register permits cross-origin
requests.

| Register | Cross-origin policy | How the page gets the data |
|---|---|---|
| ClinicalTrials.gov v2 | `Access-Control-Allow-Origin: *` | Queried live from the browser |
| ISRCTN | Reflects the requesting origin | Queried live from the browser (XML) |
| EU CTIS | Response pinned to `https://euclinicaltrials.eu` | **Cannot** be read by a browser — see below |

CTIS advertises `*` on the preflight but returns its own origin on the actual
POST, so the browser blocks the read. `scripts/build_snapshot.py` therefore runs
server-side in GitHub Actions and commits `docs/data/eu_ctis.json`, which the
page loads same-origin. The page shows the snapshot date so nobody mistakes it
for live data.

### Recruitment status is derived for two of the three

Only ClinicalTrials.gov publishes a plain recruitment-status field.

- **ISRCTN** has no such field in the API feed. The page uses
  `recruitmentStatusOverride` when the registry has set one, and otherwise works
  out open/closed from the published recruitment start and end dates.
- **CTIS** publishes no single recruitment field either. The snapshot derives it
  per member state from notification events — a country counts as recruiting
  when it has a `START_OF_RECRUITMENT` with no matching `END_OF_RECRUITMENT` or
  `END_OF_TRIAL`.

Both derivations are labelled in the interface rather than presented as
registry-stated fact.

### Duplicates across registers

The same study is often registered in more than one place — 5G-PEARL, for
instance, is both `ISRCTN88772403` and `NCT07391215`. There is no shared key, so
the page flags *likely* pairs by shared study acronym or identical normalised
title and says "possibly the same study as …". It never merges records. Both
stay visible.

---

## Publishing to GitHub Pages

1. **Settings → Pages → Source: GitHub Actions.**
2. Push the contents of this repo including `docs/` and
   `.github/workflows/refresh-trials.yml`.
3. The workflow runs on push, daily at 04:15 UTC, and on manual dispatch. It
   rebuilds the EU snapshot, checks it, commits it if it changed, and deploys
   `docs/`.

The workflow refuses to publish a snapshot with fewer than 40 trials or fewer
than 10 recruiting. If the CTIS API changes shape, the job fails loudly instead
of quietly shipping an empty page.

Building the snapshot by hand:

```bash
python scripts/build_snapshot.py docs/data
```

It takes a few minutes — it searches fourteen terms, then fetches each trial's
detail record for the recruitment events. No API key, no dependencies beyond the
standard library.

---

## Fixed: the UK filter returned nothing

`ctgov_client.py` and `uk_sources.py` read `locationCity`, `locationCountry` and
`locationFacility` from ClinicalTrials.gov. The v2 API returns `city`, `country`
and `facility`. Those lookups all resolved to empty strings, so:

- `extract_row` produced a blank site for every trial, and
- the UK filter in `fetch_uk_trials` matched nothing and returned zero rows,
  with no error.

Both files now use the correct field names. On a glioblastoma search that takes
the UK result count from 0 to 22.

If you have local copies of these files, take the versions in this repo.

---

## Local development

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
streamlit run streamlit_app.py
```

The web page needs no build step, but open it over HTTP rather than `file://`
so the snapshot fetch resolves:

```bash
python -m http.server -d docs 8000
# http://localhost:8000
```

---

## Files

```
docs/index.html                      the web page — no build, no dependencies
docs/data/eu_ctis.json               EU snapshot, rebuilt daily by Actions
scripts/build_snapshot.py            builds that snapshot from the CTIS API
.github/workflows/refresh-trials.yml refresh + deploy
ctgov_client.py                      shared ClinicalTrials.gov client and scoring
uk_sources.py                        UK aggregation over ClinicalTrials.gov
streamlit_app.py                     Streamlit entrypoint
GUI_CLinicalTrial.py                 desktop/Streamlit app body
```

---

## Known limits

- The match score is a heuristic over free-text eligibility criteria, not a
  structured eligibility check. Treat it as a sort order, not a screening
  decision.
- CTIS publishes no eligibility criteria in its public feed, so CTIS records are
  scored on title, condition and phase only. Cards say so.
- ISRCTN returns up to 100 records per query and the page does not page past
  that. For glioblastoma the register holds far fewer, so this has not bitten
  yet.
- Register coverage overlaps and is incomplete. A trial absent here may still
  exist. The EudraCT legacy register and WHO ICTRP are not included.

## Licence and credit

Apache-2.0. Copyright Prajwal Ghimire.
