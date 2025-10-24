# Shared client for ClinicalTrials.gov v2 API and scoring
import re
import requests
from typing import Any, Dict, List, Tuple

DEFAULT_DIAG_TERMS = {
    "Glioblastoma": ["glioblastoma", "GBM", "glioblastoma multiforme"],
    "Diffuse midline glioma": ["diffuse midline glioma", "DMG", "H3 K27M"],
    "Anaplastic astrocytoma": ["anaplastic astrocytoma", "grade 3 astrocytoma"],
    "Astrocytoma": ["astrocytoma", "grade 2 astrocytoma", "grade 4 astrocytoma"],
    "Oligodendroglioma": ["oligodendroglioma", "1p19q codeleted"],
    "Meningioma": ["meningioma"],
    "Medulloblastoma": ["medulloblastoma"],
    "Ependymoma": ["ependymoma"],
    "Spinal cord tumor": ["spinal cord tumor", "spinal cord neoplasm"],
}

API_BASE = "https://clinicaltrials.gov/api/v2/studies"
UA = {"User-Agent": "BrainTrialsFinder-Desktop/1.0 (+https://clinicaltrials.gov)"}


def build_terms(diagnosis: str, keywords: str) -> List[str]:
    terms: List[str] = []
    if diagnosis in DEFAULT_DIAG_TERMS:
        terms.extend(DEFAULT_DIAG_TERMS[diagnosis])
    else:
        terms.extend(["brain tumor", "spinal cord tumor", "CNS tumor"])
    extra = [k.strip() for k in (keywords or "").split(",") if k.strip()]
    return terms + extra


def ctgov_search_one(term: str, statuses: List[str], page_size: int = 100, max_pages: int = 5) -> List[Dict[str, Any]]:
    session = requests.Session()
    session.headers.update(UA)
    all_studies: List[Dict[str, Any]] = []
    page_token = None
    count = 0
    max_iters = max_pages or 0
    while count < max_iters:
        params = {
            "query.term": term,
            "filter.overallStatus": ",".join(statuses),
            "pageSize": page_size,
        }
        if page_token:
            params["pageToken"] = page_token
        r = session.get(API_BASE, params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
        studies = data.get("studies", [])
        if not studies:
            break
        all_studies.extend(studies)
        page_token = data.get("nextPageToken")
        if not page_token:
            break
        count += 1
    return all_studies


def fetch_all_terms(terms: List[str], statuses: List[str], page_size=100, max_pages=5) -> List[Dict[str, Any]]:
    dedup: Dict[str, Dict[str, Any]] = {}
    for t in terms:
        try:
            for s in ctgov_search_one(t, statuses, page_size=page_size, max_pages=max_pages):
                ident = (s.get("protocolSection", {}) or {}).get("identificationModule", {}) or {}
                nct = ident.get("nctId")
                key = nct or id(s)
                if key not in dedup:
                    dedup[key] = s
        except requests.HTTPError:
            continue
    return list(dedup.values())


def mentions(txt: str, term: str) -> bool:
    return bool(re.search(rf"\b{re.escape(term)}\b", txt or "", re.I))


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


def parse_age_to_int(v: Any):
    if v is None:
        return None
    if isinstance(v, dict):
        return parse_age_to_int(v.get("value"))
    if isinstance(v, (int, float)):
        return int(v)
    m = re.search(r"(\d+)", str(v))
    return int(m.group(1)) if m else None


def ensure_list(v: Any):
    if v is None:
        return []
    if isinstance(v, list):
        return v
    return [v]


def score_trial(t: Dict[str, Any], intake: Dict[str, Any]) -> Tuple[int, List[str]]:
    age_local = (intake or {}).get("age")
    kps_local = (intake or {}).get("kps")
    prior_bev_local = bool((intake or {}).get("prior_bev", False))
    setting_local = (intake or {}).get("setting") or ""
    keywords_local = (intake or {}).get("keywords") or ""
    diagnosis_local = (intake or {}).get("diagnosis") or ""

    if diagnosis_local in DEFAULT_DIAG_TERMS:
        diag_terms = DEFAULT_DIAG_TERMS[diagnosis_local]
    elif diagnosis_local and diagnosis_local != "Other":
        diag_terms = [diagnosis_local]
    else:
        diag_terms = ["brain tumor", "CNS tumor", "spinal cord tumor"]

    ps = (t or {}).get("protocolSection") or {}
    elig = ps.get("eligibilityModule")
    crit = ""
    min_age = None
    max_age = None
    if isinstance(elig, dict):
        crit_raw = elig.get("eligibilityCriteria") or elig.get("criteria") or elig
        crit = as_text(crit_raw)
        min_age = parse_age_to_int(elig.get("minimumAge"))
        max_age = parse_age_to_int(elig.get("maximumAge"))
    elif isinstance(elig, str):
        crit = as_text(elig)

    phases_list = ensure_list(ps.get("designModule", {}).get("phases"))
    phases_up = [str(p).upper() for p in phases_list]
    conds_list = ensure_list(ps.get("conditionsModule", {}).get("conditions"))
    title = (ps.get("identificationModule", {}) or {}).get("briefTitle", "")

    s = 0
    reasons: List[str] = []
    if any(any(mentions(c, term) for term in diag_terms) for c in conds_list) or any(mentions(title, term) for term in diag_terms):
        s += 30
        reasons.append(f"Matches diagnosis: {diagnosis_local or 'neuro-oncology'}.")
    if any("PHASE 2" in p or "PHASE2" in p for p in phases_up):
        s += 8
    if any("PHASE 3" in p or "PHASE3" in p for p in phases_up):
        s += 12
    try:
        if min_age is not None and age_local is not None and age_local < min_age:
            reasons.append(f"Age below minimum ({min_age}).")
            s -= 30
        if max_age is not None and age_local is not None and age_local > max_age:
            reasons.append(f"Age above maximum ({max_age}).")
            s -= 30
    except Exception:
        pass
    if mentions(crit, "ECOG 0-1") and (kps_local is None or kps_local < 80):
        s -= 15
        reasons.append("Requires ECOG 0–1 (KPS ~≥80).")
    if mentions(crit, "Karnofsky") and (kps_local is None or kps_local < 70):
        s -= 10
        reasons.append("Requires KPS ≥70.")
    if prior_bev_local and mentions(crit, "no prior bevacizumab"):
        s -= 25
        reasons.append("Excludes prior bevacizumab.")
    if setting_local == "Recurrent" and mentions(crit, "recurrent"):
        s += 8
    if setting_local == "Newly diagnosed" and (mentions(crit, "newly diagnosed") or mentions(title, "adjuvant")):
        s += 8
    for kw in [k.strip() for k in (keywords_local or "").split(",") if k.strip()]:
        if mentions(title, kw) or mentions(crit, kw):
            s += 3
    return max(0, min(100, s)), reasons
# python
def extract_row(study: dict) -> dict:
    """Return a flat row dict for the table/PDF. Safe against missing fields."""
    ps = (study.get("protocolSection") or {})
    idm = (ps.get("identificationModule") or {})
    scm = (ps.get("statusModule") or {})
    dsm = (ps.get("designModule") or {})
    cdnm = (ps.get("conditionsModule") or {})
    slm = (ps.get("sponsorCollaboratorsModule") or {})
    clm = (ps.get("contactsLocationsModule") or {})

    title = (idm.get("officialTitle") or idm.get("briefTitle") or "").strip()
    nct = (idm.get("nctId") or "").strip()

    status_raw = (scm.get("overallStatus") or "").strip()
    # e.g., RECRUITING -> Recruiting
    status = status_raw.replace("_", " ").title() if status_raw else ""

    phases_list = ensure_list(dsm.get("phases"))
    phases = ", ".join(phases_list)

    conditions = ", ".join(ensure_list(cdnm.get("conditions")))

    sponsor = ""
    lead = slm.get("leadSponsor") or {}
    if isinstance(lead, dict):
        sponsor = (lead.get("name") or "").strip()

    city_country = ""
    locs = ensure_list(clm.get("locations"))
    if locs:
        first = locs[0]
        city = (first.get("locationCity") or "").strip()
        country = (first.get("locationCountry") or "").strip()
        parts = [p for p in [city, country] if p]
        city_country = ", ".join(parts)

    return {
        "title": title,
        "nct": nct,
        "status": status,
        "phases": phases,
        "conditions": conditions,
        "sponsor": sponsor,
        "city_country": city_country,
    }
