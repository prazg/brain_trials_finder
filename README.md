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
| **Streamlit app** | `streamlit run streamlit_app.py` | ClinicalTrials.gov |
| **Desktop app** | `python desktop_app.py`, or the Windows build `Brain Cancer Trial Finder.exe` | ClinicalTrials.gov, plus deep links to CRUK / NIHR / ISRCTN |

**This is a search aid, not medical advice.** Eligibility is decided by the trial
team. Always confirm with the treating clinician and the trial contact.

---

## The web page

It loads with a glioblastoma / United Kingdom search already run, so there is something on
screen immediately.

- **Diagnosis** picks a synonym set (Glioblastoma also searches GBM, glioblastoma
  multiforme, gliosarcoma) rather than a single string.
- **Must also mention** narrows the results to trials that mention at least one
  of your keywords (a drug, a device, a biomarker). It narrows every register
  the same way.
- **Region** filters by where the trial *sites* are, not where it is registered,
  so a US-sponsored trial recruiting in London appears under United Kingdom.
- **Patient factors** — age, KPS, newly diagnosed vs recurrent, prior
  bevacizumab — feed the match score. They re-rank the list as you type, without
  another request, and they never leave the browser.
- **Every score is itemised.** Each card lists what added points and what
  subtracted them ("Phase 2", "Maximum age 39", "Needs ECOG 0–1"), so a score of
  0 is explained rather than mysterious. The weights are listed on the page.
- **Each card opens out** to its sites, the eligibility criteria as the register
  published them, and (for ClinicalTrials.gov) the trial's central contact.
- **Filter box** above the results narrows what is on screen by hospital, city,
  sponsor, drug or trial ID.
- **Nothing disappears silently.** A line above the results counts what was
  fetched but is not shown, and why (closed, observational, wrong region…).
- **Copy link** puts the whole search in the address so it can be bookmarked or
  sent to a colleague. It lives in the URL fragment, which is not sent to any
  server.
- Results export to CSV with the scoring reasons included.

### How the registers are reached

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
`scripts/build_snapshot.py` runs server-side and commits the result. The search
feed carries no eligibility text or site names, so the script also reads each
trial's detail record for its principal inclusion and exclusion criteria, its
sites (facility, city, country — no named contacts are stored) and the NCT
number the sponsor entered.

**India and China are reached through WHO ICTRP, not directly.** CTRI serves a
PHP form that returned no records for the obvious POST field names and
publishes no API; ChiCTR answers HTTP 405 to both GET and POST on its search
paths. Neither sends CORS headers, so a static page could not read them even if
they did respond. ICTRP is the WHO aggregation point both feed into, and its
per-trial records carry registry, recruitment status, countries, phase,
condition and full eligibility criteria. One verified route beats two brittle
scrapers.

`scripts/build_ictrp.py` collects it. The snapshot built on 10 November 2026
holds 161 trials across JPRN (Japan), CTRI (India), NTR/OMON (Netherlands),
ChiCTR (China), ANZCTR, DRKS (Germany), IRCT (Iran), TCTR (Thailand), ReBec,
PACTR, REPEC and RPCEC; the CTIS snapshot of the same date holds 164.

Records from ClinicalTrials.gov and ISRCTN are dropped from the ICTRP snapshot,
since the page queries those live. EUCTR records are dropped too — that is the
retired EU register, it duplicates one row per member state, and CTIS supersedes
it.

Both snapshots show their build date in the interface, so nobody mistakes them
for live data, and a snapshot more than a week old is flagged as such. The page
also links out to each register's own search, because ICTRP mirrors national
registries on their own schedule and a very new CTRI or ChiCTR entry can appear
on the national site first.

### Recruitment status is derived for two of the registers

Only ClinicalTrials.gov publishes a plain recruitment-status field.

- **ISRCTN** has no such field in the API feed. The page uses the registry's own
  status override when one is set, and otherwise works out open/closed from the
  published recruitment start and end dates and the overall study end date.
- **CTIS** publishes no single recruitment field either. The snapshot derives it
  per member state from dated notifications: the latest of start / restart
  versus end / halt / early termination decides, and a recorded recruitment end
  date that has passed counts as an end even when no notification was filed.
  Where that derivation says "recruiting" but the trial-level status disagrees,
  the card says the status is unclear rather than picking one.

Both derivations are labelled "(derived)" on the card rather than presented as
registry-stated fact.

### Duplicates across registers

The same study is often registered in more than one place. The page draws two
kinds of link and words them differently:

- **"Also registered as …"** — one register names the other's identifier (an
  NCT number inside a CTIS or ISRCTN record, an EU CT number inside a
  ClinicalTrials.gov record). That is the registry's own statement.
- **"Possibly the same study as …"** — a shared study acronym or an identical
  title, for example 5G-PEARL as `ISRCTN88772403` and `NCT07391215`. That is
  only a likely pair.

It never merges records. Both stay visible.

### Records that match by accident

"GBM" also abbreviates *glomerular basement membrane*, so a glioblastoma search
pulls a few anti-GBM kidney disease and vasculitis trials out of the registers.
ISRCTN searches the whole record, so "brain metastases" also returns prostate
and bowel cancer trials that only list brain metastases as an exclusion. Records
whose own title and condition name no brain or spine tumour are hidden by
default, counted in the line above the results, and one click shows them.

---

## The Streamlit and desktop apps

Both search ClinicalTrials.gov through the shared client in `ctgov_client.py`
and use the same scoring as the web page.

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

pip install -r requirements.txt            # Streamlit app
streamlit run streamlit_app.py

pip install -r requirements-desktop.txt    # desktop app (Tkinter + PDF export)
python desktop_app.py

python Neuro_onc_clinicalTrial.py --diagnosis Glioblastoma --country "United Kingdom"   # CSV/JSON export
```

**The bundled `Brain Cancer Trial Finder.exe` predates version 2.2** and still
has the older search behaviour. Rebuild it from `desktop_app.py` either on
Windows:

```bash
pip install -r requirements-desktop.txt pyinstaller
pyinstaller --onefile --windowed --name "Brain Cancer Trial Finder" desktop_app.py
```

or without a Windows machine, by running the *Build the Windows desktop app*
workflow from the repository's Actions tab and downloading its artifact.

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). It carries the register-specific traps
worth knowing before touching the parsing code — the ClinicalTrials.gov v2 field
rename, where ISRCTN actually keeps its eligibility fields, the CTIS preflight
that lies about CORS, and ICTRP's paging window. [CHANGELOG.md](CHANGELOG.md)
lists what changed in each version.

```bash
npm install
npm run test:unit     # page behaviour against fixtures, no network, a few seconds
npm run test:python   # shared client and snapshot builders, no network
npm run test:live     # the real page against the live registers
npm run serve         # http://localhost:8000
```

The web page needs no build step, but open it over HTTP rather than `file://`
so the snapshot fetch resolves.

### Publishing

`.github/workflows/refresh-trials.yml` is the only workflow that publishes. Every
day it rebuilds both snapshots, commits them, runs the offline tests and deploys
the `docs/` folder to GitHub Pages; a push to `main` that touches the page
deploys without rebuilding. In the repository settings, **Pages → Source** must
be set to **GitHub Actions**.

---

## Files

```
docs/index.html                      the web page — no build, no dependencies
docs/data/eu_ctis.json               EU CTIS snapshot, rebuilt daily
docs/data/ictrp.json                 WHO ICTRP snapshot (India, China, others)
scripts/build_snapshot.py            builds the CTIS snapshot
scripts/build_ictrp.py               builds the ICTRP snapshot, resumable
test/unit.test.mjs                   offline page tests (fixtures)
test/page.test.mjs                   live integration test, runs the real page
test/test_python.py                  offline tests for the client and the builders
.github/workflows/refresh-trials.yml refresh + test + deploy
.github/workflows/ci.yml             offline tests on pull requests
.github/workflows/build-desktop.yml  builds the Windows executable on demand
ctgov_client.py                      shared ClinicalTrials.gov client and scoring
uk_sources.py                        UK search over ClinicalTrials.gov
streamlit_app.py                     Streamlit entrypoint
GUI_CLinicalTrial.py                 Streamlit app body
desktop_app.py                       Tkinter desktop app
Neuro_onc_clinicalTrial.py           command-line CSV/JSON export
```

---

## Known limits

- The match score is a heuristic over free-text eligibility criteria, not a
  structured eligibility check. Treat it as a sort order, not a screening
  decision.
- ClinicalTrials.gov results arrive 100 at a time. "Best match" sorts what has
  been fetched so far; use *Load more* for the rest of a large result set.
- About one in five CTIS records has no eligibility text in its detail record.
  Those are scored on title, condition, phase and age band only, and the card
  says so.
- CTIS publishes age bands (0–17, 18–64, 65+), not limits, so its age check is
  coarse.
- ICTRP publishes no age fields in its 24-element public record, so those
  trials are scored without an age check.
- Where ICTRP lists no country for a record from a single-country registry
  (JPRN, CTRI, ChiCTR…), the page uses that registry's country and says the
  country was inferred.
- ICTRP searches are walked 220 records deep per term, newest first, so an old
  national trial that is still recruiting can be missed.
- Chinese coverage of glioblastoma specifically is thin, because ChiCTR entries
  tend to be indexed under "glioma" rather than "glioblastoma". Searching
  *Glioma (any grade)* surfaces considerably more.
- The phase filter is strict: choosing a phase hides trials that state no phase,
  and the line above the results says how many.
- Register coverage overlaps and is incomplete. A trial absent here may still
  exist. The retired EudraCT register is deliberately excluded.

## Licence and credit

Apache-2.0. Copyright Prajwal Ghimire 2026.
