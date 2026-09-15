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
pip install -r requirements.txt
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
test/page.test.mjs         integration test, runs the real page
ctgov_client.py            shared ClinicalTrials.gov client and scoring
uk_sources.py              UK aggregation
streamlit_app.py           Streamlit entrypoint
GUI_CLinicalTrial.py       app body
```

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
npm test                 # ten scenarios across all six sources
npm test -- --quick      # one scenario, fast loop
```

The suite loads the real page in a real DOM and **hits ClinicalTrials.gov and
ISRCTN over the network**. That's on purpose. The failure mode this project is
most exposed to is a register quietly renaming a field so the page renders
blanks without throwing anything — a mocked test would sail straight past it.

It fails the build on a failed source, a blank title, a missing trial id, or a
result count below a per-scenario floor.

Run it before opening a pull request, and paste the output in. A change to
parsing, scoring, or filtering without test output attached is hard to review.

If a scenario's minimum starts failing because trials genuinely closed, lower the
floor in `SCENARIOS` in the same PR and say so. Don't delete the scenario.

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
exposes no finer public label and we do not invent one. Recruitment is derived
per member state from `START_OF_RECRUITMENT` notifications without a matching
`END_OF_RECRUITMENT` or `END_OF_TRIAL`. Codes 6, 8 and 11 are Halted, Ended and
Not authorised, confirmed by retrieving examples.

**ICTRP's pager shows ten pages at a time and must be walked in order.** Jumping
straight to `Page$12` returns nothing; walking 2 → 11 shifts the window to
12–21. It also throttles sustained traffic with HTTP 403, so the collector backs
off 45 seconds and is resumable.

**ICTRP study types are not binary.** Alongside "Interventional" and
"Observational" you'll meet "Diagnostic test", "Prognosis study" and "Relative
factors research". Test for `/intervention/i` positively rather than treating
everything non-observational as interventional.

**ICTRP's public record has 24 elements and no age fields.** Those trials are
scored without an age check and the card says criteria are missing. Don't paper
over it by parsing ages out of free-text inclusion criteria — it is not reliable
enough to drive a filter that hides trials from someone.

## Common changes

### Adding a diagnosis

Add an entry to `DIAGNOSES` in `index.html`. The key is the label; the value is
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
python scripts/build_snapshot.py docs/data     # CTIS, a few minutes
python scripts/build_ictrp.py    docs/data     # ICTRP, 30-60 minutes
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
- `npm test` output
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
