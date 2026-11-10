#!/usr/bin/env python3
"""
Build the EU CTIS snapshot consumed by docs/index.html.

Why this exists
---------------
ClinicalTrials.gov and ISRCTN both send permissive CORS headers, so the static
page queries them directly from the browser. The EU CTIS public API does not:
its preflight advertises `Access-Control-Allow-Origin: *`, but the actual POST
response is pinned to `https://euclinicaltrials.eu`, so a browser blocks the
read. This script runs server-side (GitHub Actions) and commits the result as
JSON that the page can load same-origin.

Verified API behaviour (re-checked against the live service, November 2026):
  POST https://euclinicaltrials.eu/ctis-public-api/search
       body {"pagination":{"page":N,"size":M},"searchCriteria":{...}}
       searchCriteria.containAll = free-text
       searchCriteria.status     = list of numeric status codes
  GET  https://euclinicaltrials.eu/ctis-public-api/retrieve/{ctNumber}
       .events.trialEvents[].events[]          {notificationType, date}
       .authorizedApplication.authorizedPartsII[]
            .mscInfo.activeTrialRecruitmentPeriod.{recruitmentStartDate,recruitmentEndDate}
            .trialSites[].organisationAddressInfo.{organisation.name,address.city,address.countryName}
       .authorizedApplication.authorizedPartI.trialDetails
            .trialInformation.eligibilityCriteria.{principalInclusionCriteria,principalExclusionCriteria}[]
            .clinicalTrialIdentifiers.secondaryIdentifyingNumbers.nctNumber.number

How recruitment is derived
--------------------------
CTIS publishes no single recruitment field. Three signals exist and none is
sufficient alone, so they are combined conservatively:

  1. Dated notifications per member state. START_OF_RECRUITMENT and
     RESTART_OF_RECRUITMENT open recruitment; END_OF_RECRUITMENT, END_OF_TRIAL,
     TEMPORARY_HALT and EARLY_TERMINATION close it. The *latest* one wins, and
     a tie on the same day goes to "closed".
  2. mscInfo.activeTrialRecruitmentPeriod.recruitmentEndDate. Where an
     END_OF_RECRUITMENT notification exists this date equals it, but some
     records carry the end date with no notification at all. An end date that
     has passed is therefore treated as a closing event too. (The first version
     of this script ignored it and reported such trials as recruiting.)
  3. The trial-level status code. Codes 2-5 all resolve to ctStatus
     "Authorised" and the API exposes no finer label, so none is invented here.
     Empirically, though, every trial the events mark as closed carries code 5
     and none carries code 4. When events say "recruiting" but the code is 5
     the two disagree, and the snapshot says so ("uncertain") instead of
     picking a side.

Status codes: 6 = Halted, 8 = Ended, 11 = Not authorised were confirmed by
retrieving examples.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

API_SEARCH = "https://euclinicaltrials.eu/ctis-public-api/search"
API_RETRIEVE = "https://euclinicaltrials.eu/ctis-public-api/retrieve/{ct}"

UA = "brain-trials-finder/2.2 (+https://github.com/prazg/brain_trials_finder)"

SCHEMA = 2

# Status codes whose trial-level ctStatus resolves to "Authorised".
# These are the only ones that can plausibly still be recruiting.
AUTHORISED_CODES = [2, 3, 4, 5]

STATUS_LABELS = {
    2: "Authorised",
    3: "Authorised",
    4: "Authorised",
    5: "Authorised",
    6: "Halted",
    8: "Ended",
    11: "Not authorised",
}

# See "How recruitment is derived", point 3.
CODE_RECRUITMENT_ENDED = 5

SEARCH_TERMS = [
    "glioblastoma",
    "glioma",
    "astrocytoma",
    "oligodendroglioma",
    "diffuse midline glioma",
    "meningioma",
    "medulloblastoma",
    "ependymoma",
    "brain tumour",
    "brain tumor",
    "brain metastases",
    "brain metastasis",
    "leptomeningeal",
    "central nervous system tumour",
    "central nervous system lymphoma",
    "spinal cord tumour",
    "spinal cord tumor",
    "diffuse intrinsic pontine glioma",
]

PAGE_SIZE = 100
RETRY = 4
PAUSE = 0.25
MAX_CRITERIA_CHARS = 6000
MAX_SITES = 250

OPENING = ("START_OF_RECRUITMENT", "RESTART_OF_RECRUITMENT")
CLOSING = ("END_OF_RECRUITMENT", "END_OF_TRIAL", "TEMPORARY_HALT", "EARLY_TERMINATION")


def _request(url: str, body: dict | None = None, timeout: int = 90):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"User-Agent": UA, "Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers)
    last: Exception | None = None
    for attempt in range(RETRY):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            last = exc
            # 4xx other than throttling will not get better on a retry.
            if 400 <= exc.code < 500 and exc.code not in (408, 429):
                break
            time.sleep(2.0 * (attempt + 1))
        except (urllib.error.URLError, TimeoutError, ConnectionError, json.JSONDecodeError) as exc:
            last = exc
            time.sleep(2.0 * (attempt + 1))
    raise RuntimeError(f"{url} failed after {RETRY} attempts: {last}")


def search_term(term: str) -> list[dict]:
    """All Authorised-family records matching a free-text term."""
    out: list[dict] = []
    page = 1
    while True:
        payload = {
            "pagination": {"page": page, "size": PAGE_SIZE},
            "searchCriteria": {"containAll": term, "status": AUTHORISED_CODES},
        }
        res = _request(API_SEARCH, payload)
        out.extend(res.get("data") or [])
        pg = res.get("pagination") or {}
        if not pg.get("nextPage") or page > 50:
            break
        page += 1
        time.sleep(PAUSE)
    return out


def iso_date(value) -> str:
    """CTIS mixes 'DD/MM/YYYY' (search rows) and ISO (detail). Return ISO or ''."""
    s = str(value or "").strip()
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return m.group(0)
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", s)
    if m:
        return f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
    return ""


def country_states(detail: dict, today: str) -> dict[str, str]:
    """
    Per member state: 'recruiting', 'closed' or 'not_started'.
    `today` is an ISO date, passed in so the logic is testable.
    """
    timeline: dict[str, list[tuple[str, int]]] = {}

    for entry in ((detail.get("events") or {}).get("trialEvents") or []):
        name = entry.get("mscName")
        if not name:
            continue
        rows = timeline.setdefault(name, [])
        for ev in entry.get("events") or []:
            kind = ev.get("notificationType") or ""
            # An undated closing event is treated as decisive (sorts last);
            # an undated opening event sorts first. Both err towards "closed".
            if kind in OPENING:
                rows.append((iso_date(ev.get("date")) or "0000-00-00", 0))
            elif kind in CLOSING:
                rows.append((iso_date(ev.get("date")) or "9999-99-99", 1))

    parts = (detail.get("authorizedApplication") or {}).get("authorizedPartsII") or []
    for part in parts:
        msc = part.get("mscInfo") or {}
        name = msc.get("mscName")
        if not name:
            continue
        rows = timeline.setdefault(name, [])
        period = msc.get("activeTrialRecruitmentPeriod") or {}
        end = iso_date(period.get("recruitmentEndDate"))
        if end and end <= today:
            rows.append((end, 1))

    states: dict[str, str] = {}
    for name, rows in timeline.items():
        if not any(kind == 0 for _, kind in rows):
            states[name] = "not_started"
            continue
        # Sort by date, closing after opening on the same day.
        latest = sorted(rows)[-1]
        states[name] = "closed" if latest[1] == 1 else "recruiting"
    return states


def recruitment_from_detail(detail: dict, status_code, today: str) -> tuple[str, list[str]]:
    """
    Returns (state, recruiting_countries).
    state: recruiting | closed | not_started | uncertain | unknown
    """
    if not detail:
        return "unknown", []
    states = country_states(detail, today)
    if not states:
        return "unknown", []
    open_in = sorted(c for c, s in states.items() if s == "recruiting")
    if open_in:
        if status_code == CODE_RECRUITMENT_ENDED:
            return "uncertain", open_in
        return "recruiting", open_in
    if any(s == "closed" for s in states.values()):
        return "closed", []
    return "not_started", []


def _clean(text) -> str:
    return re.sub(r"[ \t\r\f\v]+", " ", str(text or "")).strip()


def criteria_from_detail(detail: dict) -> str:
    info = ((((detail.get("authorizedApplication") or {}).get("authorizedPartI") or {})
             .get("trialDetails") or {}).get("trialInformation") or {})
    elig = info.get("eligibilityCriteria") or {}

    def lines(key: str) -> list[str]:
        rows = sorted(elig.get(key) or [], key=lambda r: r.get("number") or 0)
        return [_clean(r.get(key)) for r in rows if _clean(r.get(key))]

    inc, exc = lines("principalInclusionCriteria"), lines("principalExclusionCriteria")
    blocks = []
    if inc:
        blocks.append("Inclusion:\n" + "\n".join("- " + x for x in inc))
    if exc:
        blocks.append("Exclusion:\n" + "\n".join("- " + x for x in exc))
    return "\n\n".join(blocks)[:MAX_CRITERIA_CHARS]


def sites_from_detail(detail: dict) -> list[dict]:
    """Facility, city and country only. Site contact people are deliberately left out."""
    out: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    parts = (detail.get("authorizedApplication") or {}).get("authorizedPartsII") or []
    for part in parts:
        msc_name = (part.get("mscInfo") or {}).get("mscName") or ""
        for site in part.get("trialSites") or []:
            info = site.get("organisationAddressInfo") or {}
            addr = info.get("address") or {}
            row = (
                _clean((info.get("organisation") or {}).get("name")),
                _clean(addr.get("city")),
                _clean(addr.get("countryName")) or msc_name,
            )
            if row in seen or not any(row):
                continue
            seen.add(row)
            out.append({"facility": row[0], "city": row[1], "country": row[2]})
    return out[:MAX_SITES]


def other_ids_from_detail(detail: dict) -> list[str]:
    """Cross-register identifiers the sponsor entered, e.g. the NCT number."""
    ids = ((((detail.get("authorizedApplication") or {}).get("authorizedPartI") or {})
            .get("trialDetails") or {}).get("clinicalTrialIdentifiers") or {})
    sec = ids.get("secondaryIdentifyingNumbers") or {}
    found: list[str] = []
    raw = [(sec.get("nctNumber") or {}).get("number")]
    raw += [r.get("number") for r in (sec.get("additionalRegistries") or []) if isinstance(r, dict)]
    for value in raw:
        for m in re.findall(r"NCT\d{8}|ISRCTN\d{8}", str(value or "").upper().replace(" ", "")):
            if m not in found:
                found.append(m)
    return found


def build(previous: dict | None = None, today: str | None = None) -> dict:
    today = today or date.today().isoformat()
    prev_by_id = {t.get("id"): t for t in (previous or {}).get("trials", [])}

    seen: dict[str, dict] = {}
    failed_terms: list[str] = []
    for term in SEARCH_TERMS:
        try:
            rows = search_term(term)
        except RuntimeError as exc:
            print(f"  ! {term}: {exc}", file=sys.stderr)
            failed_terms.append(term)
            continue
        new = 0
        for row in rows:
            ct = row.get("ctNumber")
            if ct and ct not in seen:
                seen[ct] = row
                new += 1
        print(f"  {term}: {len(rows)} hits, {new} new (running total {len(seen)})")
        time.sleep(PAUSE)

    trials = []
    stale = 0
    total = len(seen)
    for i, (ct, row) in enumerate(sorted(seen.items()), 1):
        if i % 25 == 0 or i == total:
            print(f"  detail {i}/{total}")
        code = row.get("ctStatus")
        record = {
            "register": "CTIS",
            "id": ct,
            "title": _clean(row.get("ctTitle")),
            "conditions": _clean(row.get("conditions")),
            "sponsor": _clean(row.get("sponsor")),
            "sponsorType": row.get("sponsorType"),
            "phase": row.get("trialPhase"),
            "countries": parse_countries(row.get("trialCountries")),
            "statusCode": code,
            "statusLabel": STATUS_LABELS.get(code, "Unknown"),
            "ageGroup": row.get("ageGroup"),
            "gender": row.get("gender"),
            "enrolled": row.get("totalNumberEnrolled"),
            "startDate": iso_date(row.get("startDateEU")),
            "lastUpdated": iso_date(row.get("lastUpdated")),
            "url": f"https://euclinicaltrials.eu/ctis-public/view/{ct}",
        }
        try:
            detail = _request(API_RETRIEVE.format(ct=ct))
        except RuntimeError as exc:
            print(f"  ! detail {ct}: {exc}", file=sys.stderr)
            detail = None

        if detail:
            state, recruiting_in = recruitment_from_detail(detail, code, today)
            record.update(
                recruitment=state,
                recruitingCountries=recruiting_in,
                criteria=criteria_from_detail(detail),
                sites=sites_from_detail(detail),
                otherIds=other_ids_from_detail(detail),
            )
        elif ct in prev_by_id and (previous or {}).get("schema") == SCHEMA:
            # One flaky request must not flip a trial to "unknown". Carry the
            # last good values forward and say that they are carried.
            old = prev_by_id[ct]
            record.update(
                recruitment=old.get("recruitment", "unknown"),
                recruitingCountries=old.get("recruitingCountries", []),
                criteria=old.get("criteria", ""),
                sites=old.get("sites", []),
                otherIds=old.get("otherIds", []),
                detailCarriedFrom=old.get("detailCarriedFrom")
                or (previous or {}).get("generated", "")[:10],
            )
            stale += 1
        else:
            record.update(recruitment="unknown", recruitingCountries=[], criteria="", sites=[], otherIds=[])
        trials.append(record)
        time.sleep(PAUSE)

    trials.sort(key=lambda t: (t["recruitment"] != "recruiting", t["title"].lower()))
    return {
        "schema": SCHEMA,
        "source": "EU Clinical Trials Information System (CTIS)",
        "sourceUrl": "https://euclinicaltrials.eu/ctis-public/search",
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "terms": SEARCH_TERMS,
        "failedTerms": failed_terms,
        "note": (
            "Recruitment state is derived per member state from dated notification "
            "events (latest of start/restart versus end/halt/termination) and the "
            "recorded recruitment end date. CTIS does not publish a single "
            "recruitment field. 'uncertain' means the notifications and the "
            "trial-level status disagree."
        ),
        "count": len(trials),
        "recruitingCount": sum(1 for t in trials if t["recruitment"] == "recruiting"),
        "detailCarriedForward": stale,
        "trials": trials,
    }


def parse_countries(raw) -> list[str]:
    """trialCountries arrives as ['Norway:5', 'Spain:4'] -> ['Norway', 'Spain']."""
    names = []
    for item in raw or []:
        names.append(str(item).split(":", 1)[0].strip())
    return sorted({n for n in names if n})


def write_atomic(target: Path, snapshot: dict) -> None:
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(snapshot, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, target)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("out_dir", nargs="?", default="docs/data")
    ap.add_argument("--force", action="store_true",
                    help="write even if the result looks implausibly small")
    a = ap.parse_args()

    out_dir = Path(a.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / "eu_ctis.json"

    previous = None
    if target.exists():
        try:
            previous = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"  ! could not read the previous snapshot: {exc}", file=sys.stderr)

    print("Building EU CTIS snapshot")
    snapshot = build(previous)

    # A collapse almost always means the API changed shape or throttled us, not
    # that half of Europe's neuro-oncology trials closed overnight. Keep the old
    # file in that case; a dated snapshot beats an empty one.
    prev_count = (previous or {}).get("count") or 0
    if not a.force and (snapshot["count"] == 0 or snapshot["count"] < 0.6 * prev_count):
        print(
            f"Refusing to overwrite {target}: found {snapshot['count']} trials, "
            f"previous snapshot had {prev_count}. Re-run with --force if this is real.",
            file=sys.stderr,
        )
        return 2

    write_atomic(target, snapshot)
    size_kb = target.stat().st_size / 1024
    print(
        f"Wrote {target} — {snapshot['count']} trials "
        f"({snapshot['recruitingCount']} recruiting, "
        f"{snapshot['detailCarriedForward']} with detail carried forward), {size_kb:.0f} KB"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
