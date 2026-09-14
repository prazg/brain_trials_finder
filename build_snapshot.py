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

Verified API behaviour (checked against the live service):
  POST https://euclinicaltrials.eu/ctis-public-api/search
       body {"pagination":{"page":N,"size":M},"searchCriteria":{...}}
       searchCriteria.containAll = free-text
       searchCriteria.status     = list of numeric status codes
  GET  https://euclinicaltrials.eu/ctis-public-api/retrieve/{ctNumber}
       -> .events.trialEvents[].events[].notificationType
          (START_OF_RECRUITMENT / END_OF_RECRUITMENT / START_OF_TRIAL / END_OF_TRIAL)

Status codes: 6 = Halted, 8 = Ended, 11 = Not authorised were confirmed by
retrieving examples. Codes 2, 3, 4 and 5 all report `ctStatus: "Authorised"` at
trial level; the search API does not expose a finer public label for them, so we
do NOT guess at sub-labels. Recruitment is instead derived from the per-country
notification events above, which are explicit.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

API_SEARCH = "https://euclinicaltrials.eu/ctis-public-api/search"
API_RETRIEVE = "https://euclinicaltrials.eu/ctis-public-api/retrieve/{ct}"

UA = "brain-trials-finder/2.0 (+https://github.com/prazg/brain_trials_finder)"

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
    "central nervous system tumour",
    "spinal cord tumour",
    "diffuse intrinsic pontine glioma",
]

PAGE_SIZE = 100
RETRY = 3
PAUSE = 0.25


def _request(url: str, body: dict | None = None, timeout: int = 90):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"User-Agent": UA, "Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers)
    last = None
    for attempt in range(RETRY):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode())
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last = exc
            time.sleep(1.5 * (attempt + 1))
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
        if not pg.get("nextPage"):
            break
        page += 1
        time.sleep(PAUSE)
    return out


def recruitment_from_events(detail: dict) -> tuple[str, list[str]]:
    """
    Derive recruitment state from per-member-state notifications.

    A country counts as recruiting when it has START_OF_RECRUITMENT and no
    END_OF_RECRUITMENT or END_OF_TRIAL. Returns (state, recruiting_countries).
    state is one of: recruiting | closed | not_started | unknown
    """
    events = (detail.get("events") or {})
    per_country = events.get("trialEvents") or []
    if not per_country:
        return "unknown", []

    recruiting: list[str] = []
    any_started = False
    for entry in per_country:
        kinds = {e.get("notificationType") for e in (entry.get("events") or [])}
        started = "START_OF_RECRUITMENT" in kinds
        ended = bool(kinds & {"END_OF_RECRUITMENT", "END_OF_TRIAL"})
        any_started = any_started or started
        if started and not ended:
            name = entry.get("mscName")
            if name:
                recruiting.append(name)

    if recruiting:
        return "recruiting", sorted(set(recruiting))
    if any_started:
        return "closed", []
    return "not_started", []


def parse_countries(raw) -> list[str]:
    """trialCountries arrives as ['Norway:5', 'Spain:4'] -> ['Norway', 'Spain']."""
    names = []
    for item in raw or []:
        names.append(str(item).split(":", 1)[0].strip())
    return sorted({n for n in names if n})


def build() -> dict:
    seen: dict[str, dict] = {}
    for term in SEARCH_TERMS:
        try:
            rows = search_term(term)
        except RuntimeError as exc:
            print(f"  ! {term}: {exc}", file=sys.stderr)
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
    total = len(seen)
    for i, (ct, row) in enumerate(sorted(seen.items()), 1):
        if i % 25 == 0 or i == total:
            print(f"  detail {i}/{total}")
        try:
            detail = _request(API_RETRIEVE.format(ct=ct))
        except RuntimeError as exc:
            print(f"  ! detail {ct}: {exc}", file=sys.stderr)
            detail = {}
        state, recruiting_in = recruitment_from_events(detail)
        code = row.get("ctStatus")
        trials.append(
            {
                "register": "CTIS",
                "id": ct,
                "title": (row.get("ctTitle") or "").strip(),
                "conditions": (row.get("conditions") or "").strip(),
                "sponsor": (row.get("sponsor") or "").strip(),
                "sponsorType": row.get("sponsorType"),
                "phase": row.get("trialPhase"),
                "countries": parse_countries(row.get("trialCountries")),
                "recruitingCountries": recruiting_in,
                "recruitment": state,
                "statusCode": code,
                "statusLabel": STATUS_LABELS.get(code, "Unknown"),
                "ageGroup": row.get("ageGroup"),
                "gender": row.get("gender"),
                "enrolled": row.get("totalNumberEnrolled"),
                "startDate": row.get("startDateEU"),
                "lastUpdated": row.get("lastUpdated"),
                "url": f"https://euclinicaltrials.eu/ctis-public/view/{ct}",
            }
        )
        time.sleep(PAUSE)

    trials.sort(key=lambda t: (t["recruitment"] != "recruiting", t["title"].lower()))
    return {
        "source": "EU Clinical Trials Information System (CTIS)",
        "sourceUrl": "https://euclinicaltrials.eu/ctis-public/search",
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "terms": SEARCH_TERMS,
        "note": (
            "Recruitment state is derived from per-member-state notification "
            "events (START_OF_RECRUITMENT without a matching END_OF_RECRUITMENT "
            "or END_OF_TRIAL). CTIS does not publish a single recruitment field."
        ),
        "count": len(trials),
        "recruitingCount": sum(1 for t in trials if t["recruitment"] == "recruiting"),
        "trials": trials,
    }


def main() -> int:
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("docs/data")
    out_dir.mkdir(parents=True, exist_ok=True)
    print("Building EU CTIS snapshot")
    snapshot = build()
    target = out_dir / "eu_ctis.json"
    target.write_text(json.dumps(snapshot, ensure_ascii=False, indent=1))
    size_kb = target.stat().st_size / 1024
    print(
        f"Wrote {target} — {snapshot['count']} trials "
        f"({snapshot['recruitingCount']} recruiting), {size_kb:.0f} KB"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
