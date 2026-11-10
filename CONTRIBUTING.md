# Contributing

Thanks for looking. This is a small project with an unusual amount of hard-won
knowledge baked into it, most of it about the ways six clinical trial registers
disagree with each other. This document is mainly an attempt to hand that over
so you don't rediscover it painfully.

## The one rule that matters

**People use this to look for treatment options for themselves or someone they
love.** That sets a bar.

- Never present a derived value as if the register stated it. If recruitment
  status was worked out from dates rather than published as a field, the
  interface has to say so. Two of the six sources are in this position and both
  are labelled.
- Never silently drop or merge trials. If two registers hold what looks like the
  same study, flag it and show both. The user decides.
- Never guess at eligibility. The match score is a **sort order**, not a
  screening decision, and the wording throughout should keep it that way.
- If you can't verify something, say so in the interface rather than rounding it
  to a confident answer.

A pull request that makes the page look more decisive by hiding uncertainty will
be declined, however much nicer it looks.

## Getting set up

```bash
git clone https://github.com/prazg/brain_trials_finder
cd brain_trials_finder

npm install          # jsdom, for the test suite
npm run serve        # http://localhost:8000
```

Open it over HTTP, not `file://`, or the snapshot fetches won't resolve.

For the Python apps and the snapshot builders:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt            # Streamlit app
pip install -r requirements-desktop.txt    # desktop app and the Python tests
```

The two snapshot builders need nothing beyond the standard library. That's
deliberate — they run in CI and dependencies are a liability there.

## Layout

```
docs/index.html            the whole web app: no build step, no framework
docs/data/eu_ctis.json     EU CTIS snapshot
docs/data/ictrp.json       WHO ICTRP snapshot (India, China, Japan, others)
scripts/build_snapshot.py  builds the CTIS snapshot
scripts/build_ictrp.py     builds the ICTRP snapshot, resumable
test/unit.test.mjs         offline page tests against fixtures
test/page.test.mjs         live integration test, runs the real page
test/test_python.py        offline tests for the client and the builders
ctgov_client.py            shared ClinicalTrials.gov client and scoring
uk_sources.py              UK search
streamlit_app.py           Streamlit entrypoint
GUI_CLinicalTrial.py       Streamlit app body
desktop_app.py             Tkinter desktop app
Neuro_onc_clinicalTrial.py command-line export
```

Keep this layout. The workflow publishes `docs/` and the tests read `docs/`,
`scripts/` and `test/` by path, so files uploaded flat into the repository root
through the web interface will not be found. Use git, or drag whole folders.

`docs/index.html` is intentionally one file with no build step. It is a static
page that has to keep working with no toolchain and no maintenance for long
stretches. Please don't introduce a bundler, a framework, or a package that has
to be installed before the page will render. If you find yourself wanting
components, that's a signal the page is doing too much, not that it needs React.

## The architecture constraint

Everything about this design follows from one fact: **GitHub Pages serves static
files, so anything live has to happen in the visitor's browser, and that only
works where a register sends permissive CORS headers.**

| Source | What it offers | How the page reaches it |
|---|---|---|
| ClinicalTrials.gov v2 | JSON API, `Access-Control-Allow-Origin: *` | Live from the browser |
| ISRCTN | XML API, reflects the requesting origin | Live from the browser |
| EU CTIS | JSON API, response pinned to its own origin | Daily snapshot |
| WHO ICTRP | No API, ASP.NET search, no CORS | Daily snapshot |
| CTRI (India) | No documented API, no CORS | Via WHO ICTRP |
| ChiCTR (China) | HTTP 405 on its search paths, no CORS | Via WHO ICTRP |

**Before writing any code against a new source, check CORS first.** It decides
whether your work is a fetch in `index.html` or a collector in `scripts/`.
Five minutes with curl saves an afternoon:

```bash
curl -sI -H "Origin: https://example.github.io" "https://<host>/<path>" \
  | grep -i access-control
```

Watch for the trap CTIS sets: its **preflight** advertises `*` while the actual
response pins to `https://euclinicaltrials.eu`. A browser passes the preflight
and then blocks the read. Test the real request, not just `OPTIONS`.

## Testing

```bash
npm run test:unit        # page behaviour against fixtures, no network, seconds
npm run test:python      # shared client and snapshot builders, no network
npm run test:live        # eleven scenarios against the live registers
npm run test:live -- --quick
npm test                 # unit, then live
```

There are two kinds of test because there are two kinds of failure.

`test:live` loads the real page in a real DOM and **hits ClinicalTrials.gov and
ISRCTN over the network**. That's on purpose. The failure mode this project is
most exposed to is a register quietly renaming a field so the page renders
blanks without throwing anything — a mocked test would sail straight past it.
It fails on a failed source, a blank title, a missing trial id, malformed card
text, or a result count below a per-scenario floor.

`test:unit` and `test:python` use trimmed copies of real responses in
`test/fixtures/` and the committed snapshots. They pin down what the live suite
cannot, because register contents change daily: date and phase formats, country
spellings, off-topic hits, a search cancelled by a newer one, a register being
down, the CTIS recruitment derivation.

Run all three before opening a pull request, and paste the output in. A change
to parsing, scoring, or filtering without test output attached is hard to
review. If you fix a parsing bug, add the offending value to the relevant unit
test so it stays fixed.

If a live scenario's minimum starts failing because trials genuinely closed,
lower the floor in `SCENARIOS` in the same PR and say so. Don't delete the
scenario.

## Gotchas, all of them found the hard way

**ClinicalTrials.gov v2 renamed the location fields.** They are `facility`,
`city`, `country` — *not* `locationFacility`, `locationCity`, `locationCountry`.
The v1 names resolve to empty strings rather than erroring, which is how the UK
filter in `uk_sources.py` silently returned zero rows for a long time. If a
location looks blank, check this first.

**ISRCTN's useful fields are not where you'd expect.** `inclusion`, `exclusion`,
`lowerAgeLimit`, `upperAgeLimit`, `targetEnrolment`, `recruitmentStart/End`,
`recruitmentCountries` and `trialCentres` all live under `<participants>`, not
`<trialDesign>`. `phase` is under `<interventions><intervention>`. And `sponsor`
is a **sibling of `<trial>`**, so iterate `<fullTrial>` elements or you'll never
see it.

**ISRCTN records sites as England, Scotland, Wales, Northern Ireland.** Every
other register says "United Kingdom". `normCountry()` folds them; use it on any
country string entering the app from anywhere.

**Region filtering belongs on the server for ClinicalTrials.gov.** It accepts
boolean country lists via Essie syntax in `query.locn`:

```
AREA[LocationCountry]("United Kingdom" OR Germany OR France)
```

Filtering client-side instead means trimming one page of a large result set, and
UK trials scattered through 400+ global results mostly don't make the first
hundred. See `locnExpr()`.

**CTIS status codes 2, 3, 4 and 5 all resolve to "Authorised".** The search API
exposes no finer public label and we do not invent one. Codes 6, 8 and 11 are
Halted, Ended and Not authorised, confirmed by retrieving examples.

**CTIS recruitment needs dates, not just the presence of an event.** A country
can start, halt, restart and end, and the notifications arrive unordered. Take
the latest of `START_OF_RECRUITMENT` / `RESTART_OF_RECRUITMENT` versus
`END_OF_RECRUITMENT` / `END_OF_TRIAL` / `TEMPORARY_HALT` / `EARLY_TERMINATION`.
Then also read `mscInfo.activeTrialRecruitmentPeriod.recruitmentEndDate`: some
records carry an end date there with no `END_OF_RECRUITMENT` notification at
all, and ignoring it showed trials as recruiting more than a year after they
stopped. Every trial the events mark closed carries status code 5 and none
carries 4, so when events say "recruiting" on a code-5 trial the snapshot
records `uncertain` instead of choosing. `test_python.py` holds these cases.

**CTIS dates come in two formats.** Search rows say `24/07/2026`; detail records
say `2026-07-24`. ICTRP relays whatever each national registry wrote
(`01-09-2026`, `2025/06/13`, `14 September 2026`). Run every date through
`isoDate()` in the page or `iso_date()` in the builders, or "recently updated"
sorts by string and puts 2022 above 2026.

**"GBM" is also glomerular basement membrane.** A glioblastoma search returns
anti-GBM kidney disease and vasculitis trials from both live registers.
`offTopic()` hides records whose own title and condition name no brain or spine
tumour, counts them above the results, and offers them back in one click.

**ISRCTN searches the whole record, ignores `offset`, and is spelling-sensitive.**
"brain metastases" matches every oncology trial that lists brain metastases as
an exclusion criterion, which is why its own title and condition fields decide
relevance. `offset` and `page` are silently ignored, but a larger `limit` is
honoured. And "brain tumor" does not find "brain tumour": send both.

**Country names differ everywhere.** ClinicalTrials.gov returns
"Turkey (Türkiye)", ICTRP returns "Iran (Islamic Republic of)", "U.S.A",
"the US" and the occasional typo. Add new spellings to `COUNTRY_ALIASES`; a
country the region filter does not recognise silently drops the trial from that
region.

**Phase is free text outside ClinicalTrials.gov.** ICTRP alone sends "2",
"1-2", "Phase II", "Phase I,II", "Phase 2/ Phase 3", "N/A", "Not selected" and
"Retrospective study". `phaseNums()` reduces all of them to phase numbers; use
it rather than a new regex.

**ICTRP's pager shows ten pages at a time and must be walked in order.** Jumping
straight to `Page$12` returns nothing; walking 2 → 11 shifts the window to
12–21. It also throttles sustained traffic with HTTP 403, so the collector backs
off 45 seconds and is resumable.

**ICTRP study types are not binary.** Alongside "Interventional" and
"Observational" you'll meet "Diagnostic test", "Prognosis study" and "Relative
factors research". Test for `/intervention/i` positively rather than treating
everything non-observational as interventional.

**ICTRP target size is not always a number.** "Group 1:5;Group 2:5;Group 3:5;"
is fifteen participants, not 152,535. `parse_enrolment()` sums the groups.

**ICTRP detail records go stale if they are cached forever.** The collector
keeps a resume state so a throttled run can continue, but a record fetched once
and never again keeps saying "Recruiting" after the trial closes. Entries carry
a fetch time and are refetched after `--max-age-days`; a failed fetch is never
stored.

**A Streamlit entrypoint must not `import` the app.** Streamlit re-runs the
script on every interaction, and Python only executes a module on its first
import, so the page goes blank on the first click and for every visitor after
the first. `streamlit_app.py` uses `runpy.run_path` for that reason.

**Tkinter widgets must only be touched from the main thread.** The desktop app's
search threads post results to a queue that the main loop polls.

**ICTRP's public record has 24 elements and no age fields.** Those trials are
scored without an age check and the card says criteria are missing. Don't paper
over it by parsing ages out of free-text inclusion criteria — it is not reliable
enough to drive a filter that hides trials from someone.

## Common changes

### Adding a diagnosis

Add an entry to `DIAGNOSES` in `index.html` and the same entry to `DIAGNOSES`
in `ctgov_client.py`, which the Streamlit, desktop and command-line apps share. The key is the label; the value is
the synonym set actually sent to the registers. Include the abbreviations
clinicians use, since register indexing is inconsistent:

```js
"Pineal tumour": ["pineoblastoma", "pineocytoma", "pineal parenchymal tumor"],
```

Add the same terms to `SEARCH_TERMS` in both snapshot builders, otherwise CTIS
and ICTRP results will lag the live sources. Use US spellings for CTIS — its
index holds "brain tumor" and returns nothing for "brain tumour". Keep the
British spelling too; both cost nothing.

### Adding a region

Add a predicate to `REGIONS`, a branch to `locnExpr()` so ClinicalTrials.gov
filters server-side, and an `<option>` to the region select. Match on the
country strings the registers actually emit, not ISO codes.

### Adding a register

1. Check CORS, as above. That decides live fetch versus snapshot collector.
2. Write a `normaliseX()` returning the common record shape — see
   `normaliseCTG()` for the canonical version. Every field matters; `hasCriteria`
   drives an honesty chip on the card.
3. Give it a colour in the `--reg-*` custom properties and a `.trial.r-X` spine
   rule, plus a tally entry.
4. Add it to `run()` with its own `try/catch` pushing to `report`. A register
   being down must degrade that one source, never the page.
5. If any status is derived rather than published, push a line to `notices`.
6. Add a scenario to `test/page.test.mjs`.

### Rebuilding snapshots

```bash
python scripts/build_snapshot.py docs/data     # CTIS, about two minutes
python scripts/build_ictrp.py    docs/data     # ICTRP, about fifteen minutes
```

ICTRP is resumable — re-run it after an interruption, or drive it in stages:

```bash
python scripts/build_ictrp.py --phase search --terms "glioblastoma,glioma"
python scripts/build_ictrp.py --phase detail --limit 80
python scripts/build_ictrp.py --phase write docs/data
```

Please don't loop these tighter than the built-in delays. ICTRP is a free WHO
service and the pauses are there for a reason.

## Style

JavaScript: no semicolon crusades, no reformatting passes. Match what's there.
Comments should explain *why*, especially where the code looks odd because a
register is odd — those comments are the most valuable lines in the file.

Python: standard library only in `scripts/`. Type hints where they help.

Both: name things after what they are in the domain. `recruitingCountries` beats
`rc`.

## Pull requests

Keep them to one thing. Include:

- what changed and why
- `npm run test:unit`, `npm run test:python` and `npm run test:live` output
- for parsing changes, a before/after on a real trial record
- for anything touching the score, which chips change and on which trials

Say plainly what you verified and what you didn't. "I tested the UK path but not
India" is useful; quiet confidence is not.

## Reporting a wrong or missing trial

Open an issue with the trial identifier (`NCT…`, `ISRCTN…`, `CTRI/…`,
`ChiCTR…`, or a CTIS number), what the page shows, and what the register shows.
Include the diagnosis and region you searched.

Snapshot sources lag by up to a day, and ICTRP mirrors national registries on
their own schedule, so a very new CTRI or ChiCTR entry can appear on the
national site first. The page links out to each register's own search for
exactly this reason — worth checking there before filing.

## Licence

Apache-2.0. Contributions are accepted under the same licence.
