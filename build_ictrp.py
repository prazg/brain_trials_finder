#!/usr/bin/env python3
"""
Build the WHO ICTRP snapshot: the national registries that the page cannot
reach any other way — CTRI (India), ChiCTR (China), JPRN (Japan), ACTRN
(Australia/NZ), DRKS (Germany), IRCT, TCTR, KCT, PACTR and the rest.

Why go through ICTRP instead of CTRI and ChiCTR directly
--------------------------------------------------------
Checked against the live sites:
  * ctri.nic.in serves a PHP form whose POST returned no trial records with the
    obvious field names, and publishes no documented API.
  * chictr.org.cn returns HTTP 405 for both GET and POST on its search paths.
  * Neither sends CORS headers, so a static page could not read them anyway.
ICTRP is the WHO aggregation point that both feed into, it is a single
sanctioned surface, and its per-trial records carry registry, recruitment
status, countries, phase, condition and full eligibility criteria. One route,
verified, instead of three brittle scrapers.

Known limits, which the page states rather than hides
-----------------------------------------------------
  * ICTRP mirrors national registries on their own sync schedule, so a very new
    CTRI or ChiCTR record can appear on the national site first.
  * The portal is ASP.NET WebForms. Paging is a sequential postback with a
    ten-page visible window, so traversal must walk pages in order.
  * Records whose ID starts with NCT or ISRCTN are dropped: the page queries
    those two registers live. EUCTR records are dropped too — that is the
    retired EU register, it duplicates one record per member state, and CTIS
    supersedes it and is collected properly by build_snapshot.py.
"""

from __future__ import annotations

import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

PORTAL = "https://trialsearch.who.int/"
DETAIL = "https://trialsearch.who.int/Trial2.aspx?TrialID="

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/131.0.0.0 Safari/537.36 brain-trials-finder/2.1 "
      "(+https://github.com/prazg/brain_trials_finder)")

# Already covered elsewhere in the app, or superseded.
SKIP_PREFIXES = ("NCT", "ISRCTN", "EUCTR", "CTIS")

SEARCH_TERMS = [
    "glioblastoma", "glioma", "astrocytoma", "oligodendroglioma",
    "diffuse midline glioma", "meningioma", "medulloblastoma", "ependymoma",
    "brain tumor", "brain metastases", "spinal cord tumor",
    "diffuse intrinsic pontine glioma", "central nervous system lymphoma",
    "brain neoplasms",
]

MAX_PAGES = 22        # 10 records per page
PAUSE = 0.7           # be a polite guest on a WHO service; it throttles
RETRY = 4

# Country -> the region buckets the page offers.
REGISTRY_NAMES = {
    # keyed on the registry name ICTRP prints in its "source register" field
    "CTRI": "CTRI (India)",
    "ChiCTR": "ChiCTR (China)",
    "ITMCTR": "ITMCTR (China)",
    "ANZCTR": "ANZCTR (Australia/NZ)",
    "ACTRN": "ANZCTR (Australia/NZ)",
    "JPRN": "JPRN (Japan)",
    "jRCT": "jRCT (Japan)",
    "UMIN": "UMIN-CTR (Japan)",
    "DRKS": "DRKS (Germany)",
    "IRCT": "IRCT (Iran)",
    "CRIS": "CRIS (South Korea)",
    "KCT": "CRIS (South Korea)",
    "TCTR": "TCTR (Thailand)",
    "PACTR": "PACTR (Africa)",
    "ReBec": "ReBec (Brazil)",
    "REBEC": "ReBec (Brazil)",
    "RPCEC": "RPCEC (Cuba)",
    "SLCTR": "SLCTR (Sri Lanka)",
    "NTR": "NTR (Netherlands)",
    "OMON": "NTR/OMON (Netherlands)",
    "NL-OMON": "NTR/OMON (Netherlands)",
    "German Clinical Trials Register": "DRKS (Germany)",
    "RPEC": "REPEC (Peru)",
    "Clinical Trials Registry - India": "CTRI (India)",
    "Chinese Clinical Trial Registry": "ChiCTR (China)",
    "LBCTR": "LBCTR (Lebanon)",
    "REPEC": "REPEC (Peru)",
    "CRIS-KR": "CRIS (South Korea)",
}


# --------------------------------------------------------------------------
# tiny HTML helpers (stdlib only, so the Action needs no pip install)
# --------------------------------------------------------------------------
class InputScraper(HTMLParser):
    """Collect the ASP.NET __VIEWSTATE family from a WebForms page."""
    def __init__(self):
        super().__init__()
        self.fields: dict[str, str] = {}

    def handle_starttag(self, tag, attrs):
        if tag != "input":
            return
        a = dict(attrs)
        name = a.get("name", "")
        if name.startswith("__"):
            self.fields[name] = a.get("value", "") or ""


class SpanScraper(HTMLParser):
    """Collect text content keyed by span id — ICTRP detail pages use stable ids."""
    def __init__(self):
        super().__init__()
        self.spans: dict[str, list[str]] = {}
        self._stack: list[str | None] = []

    def handle_starttag(self, tag, attrs):
        if tag == "span":
            self._stack.append(dict(attrs).get("id"))

    def handle_endtag(self, tag):
        if tag == "span" and self._stack:
            self._stack.pop()

    def handle_data(self, data):
        sid = next((s for s in reversed(self._stack) if s), None)
        if sid and data.strip():
            self.spans.setdefault(sid, []).append(data.strip())


def clean(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def http(url: str, data: dict | None = None, cookies: dict | None = None):
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    headers = {"User-Agent": UA, "Accept": "text/html,application/xhtml+xml"}
    if body is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    if cookies:
        headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in cookies.items())
    req = urllib.request.Request(url, data=body, headers=headers)
    last = None
    for attempt in range(RETRY):
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                raw = r.read().decode("utf-8", "replace")
                jar = {}
                for sc in r.headers.get_all("Set-Cookie") or []:
                    k, _, v = sc.split(";")[0].partition("=")
                    jar[k.strip()] = v.strip()
                return raw, jar
        except urllib.error.HTTPError as exc:
            last = exc
            # ICTRP throttles sustained traffic with 403/429. Back off hard
            # rather than hammering a WHO service.
            if exc.code in (403, 429, 503):
                time.sleep(45 * (attempt + 1))
            else:
                time.sleep(3 * (attempt + 1))
        except (urllib.error.URLError, TimeoutError) as exc:
            last = exc
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"{url}: {last}")


def viewstate(html: str) -> dict[str, str]:
    p = InputScraper()
    p.feed(html)
    return p.fields


ID_RE = re.compile(r"TrialID=([A-Za-z0-9\-/]+)")
STATUS_RE = re.compile(
    r'GridView1_ctl(\d+)_lbl[A-Za-z]*Status[A-Za-z]*"?>([^<]*)<', re.I)


def search_ids(term: str) -> dict[str, str]:
    """Walk one search, returning {trialId: coarse status from the results grid}."""
    home, jar = http(PORTAL)
    f = viewstate(home)
    f.update({"TextBox1": term, "Button1": "Search", "chkSearchClinical": "on"})
    page, jar2 = http(PORTAL, f, jar)
    jar.update(jar2)

    total_m = re.search(r"(\d[\d,]*)\s*records", page, re.I)
    total = int(total_m.group(1).replace(",", "")) if total_m else 0

    found: dict[str, str] = {}
    for n in range(1, MAX_PAGES + 1):
        ids = list(dict.fromkeys(ID_RE.findall(page)))
        if not ids:
            break
        for tid in ids:
            found.setdefault(tid, "")
        if len(found) >= total:
            break
        nxt = viewstate(page)
        nxt.update({"__EVENTTARGET": "GridView1", "__EVENTARGUMENT": f"Page${n + 1}"})
        try:
            page, j = http(PORTAL, nxt, jar)
            jar.update(j)
        except RuntimeError:
            break
        time.sleep(PAUSE)
    return found


def fetch_detail(tid: str) -> dict | None:
    html, _ = http(DETAIL + urllib.parse.quote(tid, safe=""))
    p = SpanScraper()
    p.feed(html)
    S = {k: clean(" ".join(v)) for k, v in p.spans.items()}

    def one(suffix: str) -> str:
        for k, v in S.items():
            if k.endswith(suffix):
                return v
        return ""

    def many(suffix: str) -> list[str]:
        return [v for k, v in S.items() if k.endswith(suffix) and v]

    main_id = one("_TrialIDLabel") or tid
    title = one("_Public_titleLabel") or one("_Scientific_titleLabel")
    if not title:
        return None

    registry = one("_DescriptionLabel") or tid.split("/")[0]
    status = one("_Recruitment_statusLabel")
    countries = sorted({c for c in many("_Country_Label") if c})
    incl = one("_Inclusion_criteriaLabel")
    excl = one("_Exclusion_criteriaLabel")

    return {
        "register": "ICTRP",
        "registry": registry,
        "registryName": REGISTRY_NAMES.get(registry, registry),
        "id": main_id,
        "title": title,
        "scientificTitle": one("_Scientific_titleLabel"),
        "conditions": one("_Condition_FreeTextLabel"),
        "sponsor": one("_Primary_sponsorLabel"),
        "phase": one("_PhaseLabel"),
        "studyType": one("_Study_typeLabel"),
        "status": status,
        "recruiting": bool(re.search(r"^recruiting|^open|pending|not yet recruiting", status, re.I))
                      and not re.search(r"not recruiting|closed|complete|terminat|suspend|withdraw",
                                        status, re.I),
        "countries": countries,
        "criteria": "\n\n".join(x for x in (incl, excl) if x)[:6000],
        "enrolled": one("_Target_sizeLabel"),
        "registered": one("_Date_registrationLabel"),
        "firstEnrolment": one("_Date_enrollementLabel"),
        "lastUpdated": one("_Last_updatedLabel"),
        "url": DETAIL + urllib.parse.quote(tid, safe=""),
    }


def build(state_path: Path, phase: str, terms: list[str], limit: int) -> dict | None:
    """
    Resumable build. `state.json` holds {"ids": {...}, "detail": {...}} so the
    collection can run in chunks — useful in constrained runners and safe to
    re-enter after a timeout.
      phase=search  collect trial ids for the given terms
      phase=detail  fetch up to `limit` outstanding detail records
      phase=write   assemble the snapshot from state
    """
    state = json.loads(state_path.read_text()) if state_path.exists() else {"ids": {}, "detail": {}}

    if phase == "search":
        for term in terms:
            try:
                hits = search_ids(term)
            except RuntimeError as exc:
                print(f"  ! {term}: {exc}", file=sys.stderr)
                continue
            wanted = [t for t in hits if not t.startswith(SKIP_PREFIXES)]
            new_ids = [t for t in wanted if t not in state["ids"]]
            for t in new_ids:
                state["ids"][t] = ""
            print(f"  {term}: {len(hits)} records, {len(wanted)} national, "
                  f"{len(new_ids)} new (running total {len(state['ids'])})")
            state_path.write_text(json.dumps(state))
            time.sleep(PAUSE)
        state_path.write_text(json.dumps(state))
        return None

    if phase == "detail":
        todo = [t for t in sorted(state["ids"]) if t not in state["detail"]]
        print(f"  {len(todo)} outstanding, fetching up to {limit}")
        for i, tid in enumerate(todo[:limit], 1):
            try:
                rec = fetch_detail(tid)
            except RuntimeError as exc:
                print(f"  ! {tid}: {exc}", file=sys.stderr)
                rec = None
            state["detail"][tid] = rec
            if i % 10 == 0:
                print(f"  {i}/{min(limit, len(todo))}")
                state_path.write_text(json.dumps(state))
            time.sleep(PAUSE)
        state_path.write_text(json.dumps(state))
        print(f"  done, {len(state['detail'])}/{len(state['ids'])} resolved")
        return None

    trials = [r for r in state["detail"].values() if r]
    # Re-apply display names at write time so the mapping can be corrected
    # without refetching every detail page.
    for t in trials:
        raw = t.get("registry") or ""
        t["registryName"] = REGISTRY_NAMES.get(raw) or REGISTRY_NAMES.get(
            raw.split("-")[0]) or raw or "ICTRP"
    by_registry: dict[str, int] = {}
    for t in trials:
        by_registry[t["registryName"]] = by_registry.get(t["registryName"], 0) + 1
    trials.sort(key=lambda t: (not t["recruiting"], t["title"].lower()))
    return {
        "source": "WHO International Clinical Trials Registry Platform (ICTRP)",
        "sourceUrl": PORTAL,
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "terms": SEARCH_TERMS,
        "note": ("National registries that feed WHO ICTRP. ClinicalTrials.gov and "
                 "ISRCTN records are excluded because the page queries those live; "
                 "EUCTR is excluded as the retired EU register superseded by CTIS. "
                 "ICTRP mirrors national registries on their own schedule, so a very "
                 "new CTRI or ChiCTR entry may appear on the national site first."),
        "count": len(trials),
        "recruitingCount": sum(1 for t in trials if t["recruiting"]),
        "byRegistry": dict(sorted(by_registry.items(), key=lambda kv: -kv[1])),
        "trials": trials,
    }


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir", nargs="?", default="docs/data")
    ap.add_argument("--phase", choices=["search", "detail", "write", "all"], default="all")
    ap.add_argument("--terms", default="")
    ap.add_argument("--limit", type=int, default=100000)
    ap.add_argument("--state", default=".ictrp_state.json")
    a = ap.parse_args()

    out_dir = Path(a.out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    state_path = Path(a.state)
    terms = [t.strip() for t in a.terms.split(",") if t.strip()] or SEARCH_TERMS

    if a.phase == "all":
        build(state_path, "search", terms, a.limit)
        build(state_path, "detail", terms, a.limit)
        a.phase = "write"

    if a.phase in ("search", "detail"):
        build(state_path, a.phase, terms, a.limit)
        return 0

    snap = build(state_path, "write", terms, a.limit)
    target = out_dir / "ictrp.json"
    target.write_text(json.dumps(snap, ensure_ascii=False, indent=1))
    print(f"Wrote {target} — {snap['count']} trials "
          f"({snap['recruitingCount']} recruiting), "
          f"{target.stat().st_size / 1024:.0f} KB")
    for k, v in snap["byRegistry"].items():
        print(f"    {v:4d}  {k}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
