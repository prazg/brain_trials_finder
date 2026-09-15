# Brain Cancer Trials Finder

![PRECISE-GBM Logo](logo_precise.png)

Finds actively recruiting neuro-oncology clinical trials — glioblastoma, glioma,
brain metastases, meningioma, medulloblastoma and others — across six public
registers, and ranks them against a patient's age, performance status and
disease setting with scoring you can read.

Three ways to use it:

| | What it is | Data sources |
|---|---|---|
| **Web page** | https://prazg.github.io/brain_trials_finder/ | ClinicalTrials.gov + ISRCTN live; EU CTIS and WHO ICTRP (India, China, Japan, Australia, Germany and more) from daily snapshots |
| **Streamlit app** | `streamlit_app.py` | ClinicalTrials.gov |
| **Desktop app** | `Brain Cancer Trial Finder.exe`, built from `GUI_CLinicalTrial.py` | ClinicalTrials.gov, plus deep links to CRUK / NIHR / ISRCTN |

**This is a search aid, not medical advice.** Eligibility is decided by the trial
team. Always confirm with the treating clinician and the trial contact.

---

## The web page

It loads with a glioblastoma / United Kingdom search already run, so there is something on
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

| Source | What it offers | How the page gets the data |
|---|---|---|
| ClinicalTrials.gov v2 | JSON API, `Access-Control-Allow-Origin: *` | Queried live from the browser |
| ISRCTN | XML API, reflects the requesting origin | Queried live from the browser |
| EU CTIS | JSON API, response pinned to `https://euclinicaltrials.eu` | Daily snapshot, `docs/data/eu_ctis.json` |
| WHO ICTRP | No API. ASP.NET search, no CORS | Daily snapshot, `docs/data/ictrp.json` |
| CTRI (India) | No documented API, no CORS | Via WHO ICTRP |
| ChiCTR (China) | Returns HTTP 405 on its search paths, no CORS | Via WHO ICTRP |

Two things follow from this.

**CTIS cannot be read by a browser.** It advertises `*` on the preflight but
returns its own origin on the actual POST, so the read is blocked.
`scripts/build_snapshot.py` runs server-side and commits the result.

**India and China are reached through WHO ICTRP, not directly.** CTRI serves a
PHP form that returned no records for the obvious POST field names and
publishes no API; ChiCTR answers HTTP 405 to both GET and POST on its search
paths. Neither sends CORS headers, so a static page could not read them even if
they did respond. ICTRP is the WHO aggregation point both feed into, and its
per-trial records carry registry, recruitment status, countries, phase,
condition and full eligibility criteria. One verified route beats two brittle
scrapers.

`scripts/build_ictrp.py` collects it. Current snapshot holds 152 trials across
JPRN (Japan), CTRI (India), NTR (Netherlands), ChiCTR (China), ANZCTR, DRKS
(Germany), IRCT (Iran), TCTR (Thailand), ReBec, PACTR, REPEC and RPCEC.

Records from ClinicalTrials.gov and ISRCTN are dropped from the ICTRP snapshot,
since the page queries those live. EUCTR records are dropped too — that is the
retired EU register, it duplicates one row per member state, and CTIS supersedes
it.

Both snapshots show their build date in the interface, so nobody mistakes them
for live data. The page also links out to each register's own search, because
ICTRP mirrors national registries on their own schedule and a very new CTRI or
ChiCTR entry can appear on the national site first.

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
docs/data/eu_ctis.json               EU CTIS snapshot, rebuilt daily
docs/data/ictrp.json                 WHO ICTRP snapshot (India, China, others)
scripts/build_snapshot.py            builds the CTIS snapshot
scripts/build_ictrp.py               builds the ICTRP snapshot, resumable
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
- ICTRP publishes no age fields in its 24-element public record, so those
  trials are scored without an age check. Cards say when criteria are missing.
- Chinese coverage of glioblastoma specifically is thin, because ChiCTR entries
  tend to be indexed under "glioma" rather than "glioblastoma". Searching
  *Glioma (any grade)* surfaces considerably more.
- Register coverage overlaps and is incomplete. A trial absent here may still
  exist. The retired EudraCT register is deliberately excluded.

## Licence and credit

Apache-2.0. Copyright Prajwal Ghimire.
