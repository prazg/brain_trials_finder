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
  * Each search is walked for at most MAX_PAGES x 10 records, newest first, so a
    long-running national trial far down a large result list can be missed.
  * Records whose ID starts with NCT or ISRCTN are dropped: the page queries
    those two registers live. EUCTR records are dropped too — that is the
    retired EU register, it duplicates one record per member state, and CTIS
    supersedes it and is collected properly by build_snapshot.py.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path

PORTAL = "https://trialsearch.who.int/"
DETAIL = "https://trialsearch.who.int/Trial2.aspx?TrialID="

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/131.0.0.0 Safari/537.36 brain-trials-finder/2.2 "
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

SCHEMA = 2
MAX_PAGES = 22        # 10 records per page
PAUSE = 0.7           # be a polite guest on a WHO service; it throttles
RETRY = 4
MAX_CRITERIA_CHARS = 6000

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


# --------------------------------------------------------------------------
# field normalisers. Kept pure so tests/test_builders.py can exercise them.
# --------------------------------------------------------------------------
_NOT_OPEN = re.compile(
    r"not recruiting|no longer|stopped|closed|complete|terminat|suspend|withdraw|"
    r"halt|without start|active, not|cancel|unknown|temporar", re.I)
_OPEN = re.compile(r"^(recruiting|open|pending|not yet recruiting|enrol+ing)", re.I)


def is_recruiting(status: str) -> bool:
    """
    True for registries' many spellings of "open or about to open".

    Negatives are tested first. The earlier version tested "^recruiting" first
    and so read "Recruiting stopped after recruiting started" as open.
    """
    s = clean(status)
    if not s or _NOT_OPEN.search(s):
        return False
    return bool(_OPEN.search(s))


_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def iso_date(value: str) -> str:
    """
    ICTRP relays each registry's own date format untouched: '2025-06-13',
    '2025/06/13', '01-09-2026', '13/06/2025', '14 September 2026'. Return ISO
    'YYYY-MM-DD', or '' when it cannot be read with confidence.
    Numeric day/month order is taken as day-first, which is what the national
    registries that use it (CTRI, ANZCTR, DRKS) write.
    """
    s = clean(value)
    if not s:
        return ""
    m = re.match(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", s)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        m = re.match(r"^(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})", s)
        if m:
            d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        else:
            m = re.match(r"^(\d{1,2})\s+([A-Za-z]{3})[A-Za-z]*\.?,?\s+(\d{4})", s)
            if not m or m.group(2).lower() not in _MONTHS:
                return ""
            d, mo, y = int(m.group(1)), _MONTHS[m.group(2).lower()], int(m.group(3))
    if not (1 <= mo <= 12 and 1 <= d <= 31 and 1900 <= y <= 2100):
        return ""
    return f"{y:04d}-{mo:02d}-{d:02d}"


def parse_enrolment(value: str) -> int | None:
    """
    Target size arrives as '100', 'Case series:180;' or
    'Group 1:5;Group 2:5;Group 3:5;'. Stripping non-digits from the last form
    gives 152535 participants, so sum the per-group figures instead.
    """
    s = clean(value)
    if not s:
        return None
    if re.fullmatch(r"\d{1,7}", s):
        return int(s)
    groups = re.findall(r":\s*(\d{1,7})\s*(?:;|$)", s)
    if groups:
        return sum(int(g) for g in groups)
    return None


def cross_ids(value: str) -> list[str]:
    """NCT / ISRCTN / EU CT numbers a registry lists as secondary identifiers."""
    s = clean(value).upper()
    out: list[str] = []
    for m in re.findall(r"NCT\s?\d{8}|ISRCTN\s?\d{8}|\b20\d{2}-5\d{5}-\d{2}(?:-\d{2})?\b", s):
        m = m.replace(" ", "")
        if m not in out:
            out.append(m)
    return out


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
        "interventions": one("_Intervention_FreeTextLabel")[:600],
        "sponsor": one("_Primary_sponsorLabel"),
        "phase": one("_PhaseLabel"),
        "studyType": one("_Study_typeLabel"),
        "status": status,
        "recruiting": is_recruiting(status),
        "countries": countries,
        "criteria": "\n\n".join(x for x in (incl, excl) if x)[:MAX_CRITERIA_CHARS],
        "enrolled": parse_enrolment(one("_Target_sizeLabel")),
        "enrolledRaw": one("_Target_sizeLabel")[:120],
        "registered": iso_date(one("_Date_registrationLabel")),
        "firstEnrolment": iso_date(one("_Date_enrollementLabel")),
        "lastUpdated": iso_date(one("_Last_updatedLabel")),
        "otherIds": cross_ids(one("_SecondaryIDLabel")),
        "url": DETAIL + urllib.parse.quote(tid, safe=""),
    }


def load_state(state_path: Path) -> dict:
    """
    {"ids": {trialId: last-seen ISO date}, "detail": {trialId: {"rec": {...}, "fetched": ISO}}}
    Older state files stored the record directly and never refreshed it; those
    entries are treated as due for a refetch.
    """
    state = {"ids": {}, "detail": {}}
    if state_path.exists():
        try:
            state.update(json.loads(state_path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"  ! ignoring unreadable state file: {exc}", file=sys.stderr)
    migrated = {}
    for tid, entry in (state.get("detail") or {}).items():
        if isinstance(entry, dict) and "fetched" in entry and "rec" in entry:
            migrated[tid] = entry
        # anything else is the old layout (or a cached failure): drop it
    state["detail"] = migrated
    return state


def save_state(state_path: Path, state: dict) -> None:
    tmp = state_path.with_suffix(state_path.suffix + ".tmp")
    tmp.write_text(json.dumps(state), encoding="utf-8")
    os.replace(tmp, state_path)


def seed_state_from_snapshot(state: dict, snapshot_path: Path) -> int:
    """
    On a cold start (no cache) reuse the committed snapshot as the list of
    known ids, so a throttled search phase cannot shrink the snapshot.
    """
    if state["ids"] or not snapshot_path.exists():
        return 0
    try:
        snap = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    n = 0
    for t in snap.get("trials", []):
        # The detail URL carries the id exactly as the search results print it.
        url = t.get("url") or ""
        tid = urllib.parse.unquote(url.split("TrialID=", 1)[1]) if "TrialID=" in url else t.get("id")
        if tid and tid not in state["ids"]:
            state["ids"][tid] = ""
            n += 1
    return n


def build(state_path: Path, phase: str, terms: list[str], limit: int,
          max_age_days: int = 6, now: datetime | None = None) -> dict | None:
    """
    Resumable build, safe to re-enter after a timeout.
      phase=search  collect trial ids for the given terms
      phase=detail  fetch detail records that are missing or older than
                    max_age_days, oldest first, at most `limit` of them
      phase=write   assemble the snapshot from state
    """
    now = now or datetime.now(timezone.utc)
    state = load_state(state_path)

    if phase == "search":
        for term in terms:
            try:
                hits = search_ids(term)
            except RuntimeError as exc:
                print(f"  ! {term}: {exc}", file=sys.stderr)
                continue
            wanted = [t for t in hits if not t.upper().startswith(SKIP_PREFIXES)]
            new_ids = [t for t in wanted if t not in state["ids"]]
            for t in wanted:
                state["ids"][t] = now.date().isoformat()
            print(f"  {term}: {len(hits)} records, {len(wanted)} national, "
                  f"{len(new_ids)} new (running total {len(state['ids'])})")
            save_state(state_path, state)
            time.sleep(PAUSE)
        save_state(state_path, state)
        return None

    if phase == "detail":
        cutoff = (now - timedelta(days=max_age_days)).isoformat(timespec="seconds")
        missing = [t for t in sorted(state["ids"]) if t not in state["detail"]]
        due = sorted((t for t in state["ids"] if t in state["detail"]
                      and state["detail"][t]["fetched"] < cutoff),
                     key=lambda t: state["detail"][t]["fetched"])
        todo = (missing + due)[:limit]
        print(f"  {len(missing)} never fetched, {len(due)} older than {max_age_days} days; "
              f"fetching {len(todo)}")
        failures = 0
        for i, tid in enumerate(todo, 1):
            try:
                rec = fetch_detail(tid)
            except RuntimeError as exc:
                # Transient: leave any earlier copy in place and try again next run.
                print(f"  ! {tid}: {exc}", file=sys.stderr)
                failures += 1
                if failures >= 8 and failures > i // 2:
                    print("  ! too many failures, stopping this run early", file=sys.stderr)
                    break
                continue
            state["detail"][tid] = {"rec": rec, "fetched": now.isoformat(timespec="seconds")}
            if i % 10 == 0:
                print(f"  {i}/{len(todo)}")
                save_state(state_path, state)
            time.sleep(PAUSE)
        save_state(state_path, state)
        print(f"  done, {len(state['detail'])}/{len(state['ids'])} resolved, {failures} failed")
        return None

    trials = [e["rec"] for e in state["detail"].values() if e.get("rec")]
    # Re-apply display names and the recruiting flag at write time so either can
    # be corrected without refetching every detail page.
    for t in trials:
        raw = t.get("registry") or ""
        t["registryName"] = REGISTRY_NAMES.get(raw) or REGISTRY_NAMES.get(
            raw.split("-")[0]) or raw or "ICTRP"
        t["recruiting"] = is_recruiting(t.get("status") or "")
    by_registry: dict[str, int] = {}
    for t in trials:
        by_registry[t["registryName"]] = by_registry.get(t["registryName"], 0) + 1
    trials.sort(key=lambda t: (not t["recruiting"], t["title"].lower()))
    fetched = sorted(e["fetched"] for e in state["detail"].values() if e.get("rec"))
    return {
        "schema": SCHEMA,
        "source": "WHO International Clinical Trials Registry Platform (ICTRP)",
        "sourceUrl": PORTAL,
        "generated": now.isoformat(timespec="seconds"),
        "oldestRecord": fetched[0][:10] if fetched else "",
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
    ap = argparse.ArgumentParser(description="Build the WHO ICTRP snapshot (resumable).")
    ap.add_argument("out_dir", nargs="?", default="docs/data")
    ap.add_argument("--phase", choices=["search", "detail", "write", "all"], default="all")
    ap.add_argument("--terms", default="")
    ap.add_argument("--limit", type=int, default=100000,
                    help="most detail pages to fetch in this run")
    ap.add_argument("--max-age-days", type=int, default=6,
                    help="refetch detail pages older than this, so statuses do not go stale")
    ap.add_argument("--state", default=".ictrp_state.json")
    ap.add_argument("--force", action="store_true",
                    help="write even if the result looks implausibly small")
    a = ap.parse_args()

    out_dir = Path(a.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / "ictrp.json"
    state_path = Path(a.state)
    terms = [t.strip() for t in a.terms.split(",") if t.strip()] or SEARCH_TERMS

    if not state_path.exists():
        state = load_state(state_path)
        seeded = seed_state_from_snapshot(state, target)
        if seeded:
            print(f"  cold start: seeded {seeded} known ids from {target}")
            save_state(state_path, state)

    if a.phase == "all":
        build(state_path, "search", terms, a.limit, a.max_age_days)
        build(state_path, "detail", terms, a.limit, a.max_age_days)
        a.phase = "write"

    if a.phase in ("search", "detail"):
        build(state_path, a.phase, terms, a.limit, a.max_age_days)
        return 0

    snap = build(state_path, "write", terms, a.limit, a.max_age_days)
    prev_count = 0
    if target.exists():
        try:
            prev_count = json.loads(target.read_text(encoding="utf-8")).get("count") or 0
        except (OSError, json.JSONDecodeError):
            prev_count = 0
    if not a.force and (snap["count"] == 0 or snap["count"] < 0.6 * prev_count):
        print(f"Refusing to overwrite {target}: {snap['count']} trials resolved, "
              f"previous snapshot had {prev_count}. Re-run later, or with --force.",
              file=sys.stderr)
        return 2

    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(snap, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, target)
    print(f"Wrote {target} — {snap['count']} trials "
          f"({snap['recruitingCount']} recruiting), "
          f"{target.stat().st_size / 1024:.0f} KB")
    for k, v in snap["byRegistry"].items():
        print(f"    {v:4d}  {k}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
