"""
Shared ClinicalTrials.gov v2 client and match scoring.

Used by the Streamlit app (GUI_CLinicalTrial.py), the desktop app
(desktop_app.py), the command-line exporter (Neuro_onc_clinicalTrial.py) and
uk_sources.py, so the four of them cannot drift apart again.

Things worth knowing before changing this file:

* The v2 API names location fields `facility`, `city`, `state`, `country`.
  The v1 names (`locationFacility`, `locationCountry`, ...) do not error, they
  just come back empty. Use `study_locations()` rather than reading the raw
  dicts.
* The diagnosis goes in `query.cond` and extra keywords in `query.term`. The two
  are ANDed by the API, so keywords narrow the search. Putting keywords in the
  same OR list as the diagnosis (as earlier versions did) turns "glioblastoma,
  immunotherapy, vaccine, device" into a search for every vaccine trial on the
  register.
* Country filtering is done by the server with `query.locn`, not by fetching
  the world and trimming.

The scoring mirrors score() in docs/index.html. It is a sort order over
free-text eligibility criteria, not an eligibility decision.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests
from requests.adapters import HTTPAdapter

try:  # urllib3 ships with requests; the import path is stable across v1 and v2
    from urllib3.util.retry import Retry
except Exception:  # pragma: no cover - extremely old environments
    Retry = None  # type: ignore[assignment]

# Mirrors DIAGNOSES in docs/index.html. Keep the two in step.
DIAGNOSES: Dict[str, List[str]] = {
    "Glioblastoma": ["glioblastoma", "GBM", "glioblastoma multiforme", "gliosarcoma"],
    "Glioma (any grade)": ["glioma", "astrocytoma", "oligodendroglioma"],
    "Diffuse midline glioma": ["diffuse midline glioma", "DIPG", "H3 K27M",
                               "diffuse intrinsic pontine glioma"],
    "Astrocytoma": ["astrocytoma", "anaplastic astrocytoma"],
    "Oligodendroglioma": ["oligodendroglioma", "1p19q codeleted"],
    "Meningioma": ["meningioma"],
    "Medulloblastoma": ["medulloblastoma"],
    "Ependymoma": ["ependymoma"],
    "Brain metastases": ["brain metastases", "brain metastasis", "cerebral metastases",
                         "leptomeningeal"],
    "CNS lymphoma": ["primary CNS lymphoma", "central nervous system lymphoma"],
    "Spinal cord tumour": ["spinal cord tumor", "spinal cord neoplasm", "intramedullary tumor"],
    "Any CNS tumour": ["brain tumor", "brain neoplasm", "CNS tumor",
                       "central nervous system neoplasm"],
}
ANY_CNS = "Any CNS tumour"

# Older code and saved settings use these names.
DEFAULT_DIAG_TERMS = DIAGNOSES
_LEGACY_DIAGNOSES = {
    "Other": ANY_CNS,
    "Anaplastic astrocytoma": "Astrocytoma",
    "Spinal cord tumor": "Spinal cord tumour",
}

STATUSES = ["RECRUITING", "NOT_YET_RECRUITING"]

API_BASE = "https://clinicaltrials.gov/api/v2/studies"
UA = {"User-Agent": "BrainTrialsFinder/2.2 (+https://github.com/prazg/brain_trials_finder)"}
TIMEOUT = 30

NEURO_RE = re.compile(
    r"gliom|glioblastom|gliosarcom|astrocytom|oligodendrogli|ependymom|medulloblastom|"
    r"meningiom|brain|cerebr|intracranial|\bcns\b|central nervous|neuro-?oncolog|"
    r"leptomening|pontine|\bdipg\b|spinal cord|intramedullary|pituitar|craniopharyngiom|"
    r"schwannom|germinom|pineal", re.I)
# "GBM" is also glomerular basement membrane (anti-GBM kidney disease).
NOT_NEURO_RE = re.compile(r"glomerul|anti-?gbm|basement membrane|vasculitis|nephr|renal|kidney", re.I)


class CTGovError(RuntimeError):
    """ClinicalTrials.gov could not be reached or returned something unusable."""


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------
_session: Optional[requests.Session] = None


def get_session() -> requests.Session:
    """One shared session with polite retries on throttling and server errors."""
    global _session
    if _session is None:
        s = requests.Session()
        s.headers.update(UA)
        if Retry is not None:
            retry = Retry(total=3, connect=3, read=2, backoff_factor=0.8,
                          status_forcelist=(429, 500, 502, 503, 504),
                          allowed_methods=frozenset(["GET"]), raise_on_status=False)
            s.mount("https://", HTTPAdapter(max_retries=retry))
        _session = s
    return _session


def _essie(terms: Iterable[str]) -> str:
    """OR a list of terms, quoting the multi-word ones."""
    return " OR ".join(f'"{t}"' if re.search(r"\s", t) else t for t in terms)


def split_keywords(keywords: Any) -> List[str]:
    """'TTFields, CAR-T ,, ' -> ['TTFields', 'CAR-T']. Accepts a list too."""
    if isinstance(keywords, (list, tuple)):
        parts = [str(k) for k in keywords]
    else:
        parts = str(keywords or "").split(",")
    out: List[str] = []
    for p in parts:
        p = p.strip()
        if p and p.lower() not in (x.lower() for x in out):
            out.append(p)
    return out[:8]


def resolve_diagnosis(diagnosis: str) -> str:
    d = (diagnosis or "").strip()
    return _LEGACY_DIAGNOSES.get(d, d)


def diagnosis_terms(diagnosis: str) -> List[str]:
    """Synonym set for a diagnosis label; a free-text diagnosis searches as itself."""
    d = resolve_diagnosis(diagnosis)
    if d in DIAGNOSES:
        return list(DIAGNOSES[d])
    return [d] if d else list(DIAGNOSES[ANY_CNS])


def build_terms(diagnosis: str, keywords: str = "") -> List[str]:
    """
    Condition terms for a diagnosis.

    `keywords` is accepted for backward compatibility and deliberately ignored:
    keywords narrow the search and are passed separately (see `search`).
    """
    return diagnosis_terms(diagnosis)


def build_params(terms: List[str], keywords: Any = "", country: str = "",
                 statuses: Optional[List[str]] = None, page_size: int = 100) -> Dict[str, Any]:
    params: Dict[str, Any] = {
        "query.cond": _essie(terms),
        "filter.overallStatus": ",".join(statuses or STATUSES),
        "pageSize": max(1, min(int(page_size or 100), 1000)),
        "countTotal": "true",
    }
    kws = split_keywords(keywords)
    if kws:
        params["query.term"] = _essie(kws)
    country = (country or "").strip().strip('"')
    if country:
        params["query.locn"] = f'AREA[LocationCountry]"{country}"'
    return params


def search(diagnosis: str, keywords: Any = "", country: str = "",
           statuses: Optional[List[str]] = None, page_size: int = 100,
           max_pages: int = 5) -> Tuple[List[Dict[str, Any]], int]:
    """
    Recruiting studies for a diagnosis, narrowed by keywords and country.

    Returns (studies, total_on_register). `studies` may be shorter than the
    total when the result set is larger than page_size * max_pages.
    Raises CTGovError with a readable message if the register cannot be read.
    """
    params = build_params(diagnosis_terms(diagnosis), keywords, country, statuses, page_size)
    session = get_session()
    studies: List[Dict[str, Any]] = []
    seen: set = set()
    total = 0
    token: Optional[str] = None
    for _ in range(max(1, int(max_pages or 1))):
        page_params = dict(params)
        if token:
            page_params["pageToken"] = token
        try:
            r = session.get(API_BASE, params=page_params, timeout=TIMEOUT)
        except requests.RequestException as exc:
            raise CTGovError(
                "Could not reach ClinicalTrials.gov. Check the internet connection and "
                f"try again. ({exc.__class__.__name__})") from exc
        if r.status_code != 200:
            detail = (r.text or "").strip().splitlines()[0][:200] if r.text else ""
            raise CTGovError(f"ClinicalTrials.gov returned HTTP {r.status_code}. {detail}".strip())
        try:
            data = r.json() or {}
        except ValueError as exc:
            raise CTGovError("ClinicalTrials.gov returned a response that could not be read.") from exc
        total = int(data.get("totalCount") or total or 0)
        for s in data.get("studies") or []:
            nct = nct_id(s)
            if nct and nct in seen:
                continue
            seen.add(nct)
            studies.append(s)
        token = data.get("nextPageToken")
        if not token:
            break
    return studies, max(total, len(studies))


def fetch_all_terms(terms: List[str], statuses: Optional[List[str]] = None, page_size: int = 100,
                    max_pages: int = 5, keywords: Any = "", country: str = "") -> List[Dict[str, Any]]:
    """
    Backward-compatible wrapper: one OR query over `terms`.

    Earlier versions issued a request per term, searched the whole record
    instead of the condition, and swallowed HTTP errors. This raises CTGovError.
    """
    params = build_params(list(terms), keywords, country, statuses, page_size)
    session = get_session()
    out: List[Dict[str, Any]] = []
    seen: set = set()
    token: Optional[str] = None
    for _ in range(max(1, int(max_pages or 1))):
        page_params = dict(params)
        if token:
            page_params["pageToken"] = token
        try:
            r = session.get(API_BASE, params=page_params, timeout=TIMEOUT)
            r.raise_for_status()
            data = r.json() or {}
        except (requests.RequestException, ValueError) as exc:
            raise CTGovError(f"ClinicalTrials.gov request failed: {exc}") from exc
        for s in data.get("studies") or []:
            nct = nct_id(s)
            if nct and nct in seen:
                continue
            seen.add(nct)
            out.append(s)
        token = data.get("nextPageToken")
        if not token:
            break
    return out


# --------------------------------------------------------------------------
# Reading a study record
# --------------------------------------------------------------------------
def _ps(study: Dict[str, Any]) -> Dict[str, Any]:
    return (study or {}).get("protocolSection") or {}


def nct_id(study: Dict[str, Any]) -> str:
    return ((_ps(study).get("identificationModule") or {}).get("nctId") or "").strip()


def mentions(txt: str, term: str) -> bool:
    if not txt or not term:
        return False
    return bool(re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", txt, re.I))


def as_text(obj: Any) -> str:
    if obj is None:
        return ""
    if isinstance(obj, dict):
        for k in ("textblock", "textBlock", "value"):
            if k in obj:
                return str(obj.get(k) or "")
        return " ".join(str(v) for v in obj.values() if v is not None)
    if isinstance(obj, list):
        return "; ".join(as_text(x) for x in obj)
    return str(obj)


def parse_age_to_int(v: Any) -> Optional[int]:
    """'18 Years' -> 18, '6 Months' -> 0, '90 Days' -> 0. Whole years, rounded down."""
    if v is None or v == "":
        return None
    if isinstance(v, dict):
        return parse_age_to_int(v.get("value"))
    if isinstance(v, (int, float)):
        return int(v)
    text = str(v)
    m = re.search(r"(\d+)", text)
    if not m:
        return None
    n = int(m.group(1))
    if re.search(r"month", text, re.I):
        return n // 12
    if re.search(r"week|day|hour|minute", text, re.I):
        return 0
    return n


def ensure_list(v: Any) -> List[Any]:
    if v is None:
        return []
    if isinstance(v, list):
        return v
    return [v]


def study_locations(study: Dict[str, Any], country: str = "") -> List[Dict[str, Any]]:
    """
    Sites as plain dicts: facility, city, state, country, status, contacts.
    `country` keeps only sites whose country contains that text.
    """
    clm = _ps(study).get("contactsLocationsModule") or {}
    want = (country or "").strip().lower()
    out = []
    for loc in ensure_list(clm.get("locations")):
        if not isinstance(loc, dict):
            continue
        ctry = (loc.get("country") or "").strip()
        if want and want not in ctry.lower():
            continue
        out.append({
            "facility": (loc.get("facility") or "").strip(),
            "city": (loc.get("city") or "").strip(),
            "state": (loc.get("state") or "").strip(),
            "country": ctry,
            "status": (loc.get("status") or "").strip(),
            "contacts": [c for c in ensure_list(loc.get("contacts")) if isinstance(c, dict)],
        })
    return out


def format_site(loc: Dict[str, Any]) -> str:
    return ", ".join(p for p in (loc.get("facility"), loc.get("city"), loc.get("state"),
                                 loc.get("country")) if p)


def format_phases(phases: Any) -> str:
    """['PHASE1', 'PHASE2'] -> 'Phase 1/2'; ['NA'] -> ''."""
    nums = []
    early = False
    for p in ensure_list(phases):
        s = str(p or "").upper()
        early = early or "EARLY" in s
        nums += [int(n) for n in re.findall(r"PHASE\s*([1-4])", s)]
    if not nums:
        return ""
    label = "/".join(str(n) for n in sorted(set(nums)))
    return ("Early phase " if early else "Phase ") + label


def is_off_topic(study: Dict[str, Any]) -> bool:
    """True for records that matched a term in an unrelated sense (anti-GBM disease)."""
    ps = _ps(study)
    idm = ps.get("identificationModule") or {}
    head = " ".join([idm.get("briefTitle") or "", idm.get("officialTitle") or "",
                     " ".join(ensure_list((ps.get("conditionsModule") or {}).get("conditions")))])
    if NEURO_RE.search(head):
        return False
    if NOT_NEURO_RE.search(head):
        return True
    crit = as_text((ps.get("eligibilityModule") or {}).get("eligibilityCriteria"))
    return not NEURO_RE.search(crit)


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------
def kps_to_ecog(kps: float) -> int:
    """Approximate: KPS 100 = ECOG 0, 80-90 = 1, 60-70 = 2, 40-50 = 3, lower = 4."""
    return 0 if kps >= 100 else 1 if kps >= 80 else 2 if kps >= 60 else 3 if kps >= 40 else 4


_ECOG_MIN_KPS = [100, 80, 60, 40]
_SEP = r"\s*(?:-|–|—|to|or|,|/|and)\s*"


def ecog_max(crit: str) -> Optional[int]:
    """Highest ECOG grade the text accepts ('ECOG 0-1' -> 1), or None."""
    m = re.search(r"ECOG[^.\n;]{0,70}", crit or "", re.I)
    if not m:
        return None
    seg = m.group(0)
    hit = (re.search(r"(?:≤|<=|=<|less than or equal to)\s*([0-3])(?![0-9])", seg, re.I)
           or re.search(rf"(?:^|[^0-9])0{_SEP}(?:1{_SEP})?(?:2{_SEP})?([1-3])(?![0-9])", seg, re.I))
    return int(hit.group(1)) if hit else None


def kps_min(crit: str) -> Optional[int]:
    """Minimum Karnofsky score the text asks for ('KPS ≥ 70' -> 70), or None."""
    m = re.search(
        r"(?:karnofsky|\bKPS\b)[^.\n;]{0,60}?(?:≥|>=|=>|at least|minimum(?: of)?|"
        r"greater than or equal to|no less than)\s*(\d{2,3})(?![0-9])", crit or "", re.I)
    if not m:
        return None
    n = int(m.group(1))
    return n if 30 <= n <= 100 else None


def _number(v: Any) -> Optional[float]:
    try:
        return None if v is None or v == "" else float(v)
    except (TypeError, ValueError):
        return None


def score_trial(t: Dict[str, Any], intake: Dict[str, Any]) -> Tuple[int, List[str]]:
    """
    Returns (score 0-100, reasons). Every adjustment has a reason ending in
    its points, e.g. "Phase 2 [+8]", so a score is never unexplained.

    intake keys: diagnosis, keywords, age, kps, prior_bev, setting
    ("Recurrent" / "Newly diagnosed" / ""), country (optional).
    """
    intake = intake or {}
    age = _number(intake.get("age"))
    kps = _number(intake.get("kps"))
    prior_bev = bool(intake.get("prior_bev", False))
    setting = str(intake.get("setting") or "").lower()
    diagnosis = resolve_diagnosis(str(intake.get("diagnosis") or ""))
    terms = diagnosis_terms(diagnosis)

    ps = _ps(t)
    elig = ps.get("eligibilityModule")
    crit = ""
    min_age = max_age = None
    if isinstance(elig, dict):
        crit = as_text(elig.get("eligibilityCriteria") or "")
        min_age = parse_age_to_int(elig.get("minimumAge"))
        max_age = parse_age_to_int(elig.get("maximumAge"))
    elif isinstance(elig, str):
        crit = elig

    phases = [str(p).upper() for p in ensure_list((ps.get("designModule") or {}).get("phases"))]
    conds = [str(c) for c in ensure_list((ps.get("conditionsModule") or {}).get("conditions"))]
    idm = ps.get("identificationModule") or {}
    title = idm.get("briefTitle") or idm.get("officialTitle") or ""
    hay = (title + " " + " ".join(conds)).lower().replace("tumour", "tumor")

    s = 0
    reasons: List[str] = []

    def add(points: int, text: str) -> None:
        # The sign goes at the end so a reasons cell never starts with "+" or
        # "-", which a spreadsheet would try to run as a formula.
        nonlocal s
        s += points
        reasons.append(f"{text} [{'+' if points > 0 else '−'}{abs(points)}]" if points else f"{text} [note]")

    if any(term.lower().replace("tumour", "tumor") in hay for term in terms):
        add(30, f"Matches {(diagnosis or 'neuro-oncology').lower()}")
    elif NEURO_RE.search(hay):
        add(14, "Neuro-oncology, wider term match")

    if any(re.search(r"PHASE\s*3", p) for p in phases):
        add(12, "Phase 3")
    elif any(re.search(r"PHASE\s*2", p) for p in phases):
        add(8, "Phase 2")

    if age is not None:
        if min_age is not None and age < min_age:
            add(-30, f"Minimum age {min_age}")
        if max_age is not None and age > max_age:
            add(-30, f"Maximum age {max_age}")

    if crit:
        if kps is not None:
            need, ecog = kps_min(crit), ecog_max(crit)
            if need is not None and kps < need:
                add(-15, f"Needs KPS ≥ {need}")
            elif ecog is not None and kps_to_ecog(kps) > ecog:
                add(-15, f"Needs ECOG 0–{ecog} (about KPS ≥ {_ECOG_MIN_KPS[ecog]})")
            elif need is None and ecog is None and mentions(crit, "Karnofsky") and kps < 70:
                add(-10, "Karnofsky requirement, check criteria")
        if prior_bev and re.search(
                r"no\s+prior\s+(?:treatment\s+with\s+)?bevacizumab|bevacizumab[- ]na[ïi]ve", crit, re.I):
            add(-25, "Excludes prior bevacizumab")
        if setting.startswith("recur") and re.search(r"\b(recurrent|recurrence|relapsed)\b", crit, re.I):
            add(8, "Recurrent disease")
        if setting.startswith("new") and (mentions(crit, "newly diagnosed") or mentions(title, "adjuvant")):
            add(8, "Newly diagnosed")
    else:
        add(0, "No eligibility text published")

    if setting.startswith("recur") and re.search(r"\b(recurrent|recurrence|relapsed)\b", title, re.I):
        add(6, "Recurrent in title")

    for kw in split_keywords(intake.get("keywords")):
        if mentions(title, kw) or mentions(crit, kw):
            add(4, f"Mentions {kw}")

    country = str(intake.get("country") or "").strip()
    if country:
        open_sites = [loc for loc in study_locations(t, country)
                      if not loc["status"] or loc["status"] == "RECRUITING"]
        if open_sites:
            add(10, f"{len(open_sites)} open site{'' if len(open_sites) == 1 else 's'} in {country}")

    return max(0, min(100, s)), reasons


def extract_row(study: dict, country: str = "") -> dict:
    """
    Flat row for a table, CSV or PDF. Safe against missing fields.
    `country` picks the site shown: the first one in that country if there is
    one, otherwise the first listed.
    """
    ps = _ps(study)
    idm = ps.get("identificationModule") or {}
    scm = ps.get("statusModule") or {}
    dsm = ps.get("designModule") or {}
    cdnm = ps.get("conditionsModule") or {}
    slm = ps.get("sponsorCollaboratorsModule") or {}

    title = (idm.get("briefTitle") or idm.get("officialTitle") or "").strip()
    nct = (idm.get("nctId") or "").strip()
    status_raw = (scm.get("overallStatus") or "").strip()
    lead = slm.get("leadSponsor") or {}

    locs = study_locations(study)
    preferred = study_locations(study, country) if country else []
    first = (preferred or locs or [{}])[0]
    countries = sorted({loc["country"] for loc in locs if loc["country"]})

    return {
        "title": title,
        "nct": nct,
        "url": f"https://clinicaltrials.gov/study/{nct}" if nct else "",
        "status": status_raw.replace("_", " ").capitalize() if status_raw else "",
        "phases": format_phases(dsm.get("phases")),
        "conditions": ", ".join(str(c) for c in ensure_list(cdnm.get("conditions"))),
        "sponsor": (lead.get("name") or "").strip() if isinstance(lead, dict) else "",
        "site": format_site(first),
        "city_country": ", ".join(p for p in (first.get("city"), first.get("country")) if p),
        "countries": ", ".join(countries),
        "n_sites": len(locs),
        "updated": ((scm.get("lastUpdatePostDateStruct") or {}).get("date") or ""),
    }


def rank(studies: List[Dict[str, Any]], intake: Dict[str, Any],
         include_off_topic: bool = False) -> Tuple[List[Dict[str, Any]], int]:
    """
    Score and sort studies. Returns (rows, n_off_topic_hidden). Each row is
    extract_row() plus score, reasons (a '; '-joined string) and the raw study.
    """
    country = str((intake or {}).get("country") or "")
    rows: List[Dict[str, Any]] = []
    hidden = 0
    for s in studies:
        if not include_off_topic and is_off_topic(s):
            hidden += 1
            continue
        score, reasons = score_trial(s, intake)
        row = extract_row(s, country)
        row.update(score=score, reasons="; ".join(reasons), study=s)
        rows.append(row)
    rows.sort(key=lambda r: (-r["score"], r["title"].lower()))
    return rows, hidden
