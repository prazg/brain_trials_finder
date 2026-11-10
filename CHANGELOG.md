# Changelog

## 2.2.0 — 10 November 2026

A robustness and usability pass over the whole repository. Every item under
"Fixed" was reproduced before it was changed.

### Fixed — repository and publishing

- **The files were uploaded flat into the repository root**, but the README,
  the tests, `package.json` and the refresh workflow all expect `docs/`,
  `scripts/`, `test/` and `.github/workflows/`. As a result `npm test` could not
  run, and `refresh-trials.yml`, sitting in the root, was never picked up by
  GitHub Actions: the snapshots described as "rebuilt daily" had not been
  rebuilt since 14–15 September 2026. The layout is restored.
- `docs/data/ictrp.json` was missing from the published site (404); the page
  only found the snapshot through a fallback path.
- Two workflows would both have deployed GitHub Pages. `pages.yml` is removed;
  `refresh-trials.yml` is the single publisher and now runs the offline tests
  before it deploys.
- `.gitignore` ignored every `*.json`, so any JSON file at a new path (a
  relocated snapshot, the test fixtures, `package-lock.json`) would have been
  left out of a commit made with git.
- `GUI_CLinicalTrial.spec` built the Streamlit script as a Windows executable,
  which cannot work. Removed; the build command and a build workflow replace it.

### Fixed — web page

- **EU CTIS and WHO ICTRP dates were not ISO dates** (`24/07/2026`,
  `16 March 2026`), so "Recently updated" sorted them as text against the other
  registers' ISO dates.
- **ICTRP phases written as bare digits** ("2", "1-2") were not recognised: no
  phase score, and they passed through every phase filter.
- **Country spellings** from ICTRP ("United States of America", "U.S.A",
  "Iran (Islamic Republic of)") and ClinicalTrials.gov ("Turkey (Türkiye)") did
  not match the region lists, so those trials were dropped from their region.
- **ICTRP enrolment** such as "Group 1:5;Group 2:5;Group 3:5;" displayed as
  152,535 participants.
- **CTIS age bands** gave a minimum age but never a maximum, so an adult was not
  flagged against a children-only trial.
- **Keywords behaved differently per register**: they narrowed ClinicalTrials.gov,
  widened CTIS and ICTRP, and were ignored by ISRCTN. They now narrow all four.
- **"GBM" also means glomerular basement membrane**, and ISRCTN searches the
  whole record, so unrelated kidney, vasculitis, prostate and bowel cancer
  trials appeared under glioblastoma and brain metastases. They are now hidden
  by default, counted, and available in one click.
- "Brain tumor" did not match "brain tumour" in ISRCTN or the snapshots.
- **Load more** removed the ISRCTN, CTIS and ICTRP source chips and the
  explanatory notes.
- **Changing the region while a search was running was ignored**, leaving
  results for the old region under the new region's name. A newer search now
  cancels the older one.
- A register that never answered left the page on "Searching…" for ever. Every
  request now has a timeout and one retry, and the registers load in parallel.
- When every register failed the page said "Nothing matched". It now says the
  registers could not be reached and offers a retry.
- The score bar never drew (an inline element ignores its width).
- "n open sites in region" counted closed sites as well.
- ISRCTN trials not yet open were hidden by the recruiting filter while the
  equivalent ClinicalTrials.gov status was shown; both now count as "opening
  soon". An ISRCTN study whose overall end date has passed is no longer shown
  as open.
- The phase filter let through trials with no stated phase.
- ISRCTN results were capped at 100 with no way to page (the API ignores
  `offset`); the page now asks for up to 300 and says if more exist.
- CSV export: added a byte-order mark so Excel reads accents, neutralised cells
  that would run as formulas, and stopped revoking the download before some
  browsers had started it.
- Footer and description still described three registers.

### Fixed — snapshot builders

- **CTIS recruitment was derived without dates.** A country that halted and
  never restarted counted as recruiting; one that ended and restarted counted
  as closed; and a recorded recruitment end date with no end notification was
  ignored, which showed at least four trials as recruiting after they had
  stopped. See CONTRIBUTING for the rule now used.
- One failed detail request turned a CTIS trial to "unknown". The previous
  snapshot's values are now carried forward and labelled.
- **ICTRP "Recruiting stopped after recruiting started" was read as recruiting.**
- **The ICTRP resume state never refreshed a record and cached failures for
  ever**, so statuses could only go stale. Records now carry a fetch time and
  are refetched after six days; failures are retried.
- Neither builder protected the committed snapshot from a bad run. Both now
  write atomically and refuse to overwrite with an implausibly small result.

### Fixed — Streamlit app, desktop app, command-line export

- **Keywords were ORed with the diagnosis**, and the default keywords were
  "immunotherapy, vaccine, device". The default glioblastoma search therefore
  matched about 23,000 studies instead of about 480, fetched the first 500, and
  ranked them with a score that had no diagnosis term: the top result was a
  urinary tract infection trial.
- **Site locations were always blank**, and "Require site in country" always
  returned nothing, because the code read the v1 field names
  (`locationCountry`…) that the v2 API no longer returns.
- **The Streamlit page went blank on the first click**, and for every visitor
  after the first, because the entrypoint imported the app instead of running it.
- Streamlit showed a traceback when the register could not be reached.
- The desktop app updated widgets from worker threads.
- Ages given in months were read as years ("6 Months" as 6 years).
- HTTP errors were swallowed, giving silently partial results.
- The command-line export wrote an empty `site` column.
- PDF export could fail on titles containing `<` or `&`.

### Added

- Web page: shareable link (URL fragment), filter box, eligibility criteria and
  trial contact on each card, "Also registered as" links from register-stated
  cross-references, a count of what was fetched but not shown and why, a
  warning on snapshots older than a week, Reset, a collapsed search form on
  phones, live re-ranking as patient details change, "How the match score
  works".
- CTIS snapshot: eligibility criteria, named sites and NCT cross-references.
- ICTRP snapshot: cross-register identifiers, ISO dates, numeric enrolment.
- Streamlit app: all twelve diagnoses, country filter, CSV download, eligibility
  text. Desktop app: CSV export, column sorting, score reasons in the detail
  panel.
- Offline test suites (`npm run test:unit`, `npm run test:python`), a CI
  workflow for pull requests, a daily live check, and a workflow that builds the
  Windows executable.

### Not done

- `Brain Cancer Trial Finder.exe` is still the earlier build. It has to be
  rebuilt on Windows or with the build workflow.
- The GitHub Actions workflows were validated as YAML but have not run on
  GitHub from this change.
