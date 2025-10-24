# app.py  —  streamlit run app.py
import requests, re, math
import streamlit as st

st.set_page_config(page_title="Brain Trials Finder", layout="wide")

st.title("Brain Cancer Trials Finder (MVP)")
with st.sidebar:
    age = st.number_input("Age", 1, 100, 55)
    diagnosis = st.selectbox(
        "Diagnosis",
        [
            "Glioblastoma",
            "Diffuse midline glioma",
            "Anaplastic astrocytoma",
            "Astrocytoma",
            "Oligodendroglioma",
            "Meningioma",
            "Medulloblastoma",
            "Ependymoma",
            "Spinal cord tumor",
            "Other",
        ],
    )
    setting = st.selectbox("Disease setting", ["Newly diagnosed", "Recurrent"])
    kps = st.slider("Karnofsky (approx)", 40, 100, 80, 10)
    prior_bev = st.checkbox("Prior bevacizumab")
    prior_ttf = st.checkbox("Prior TTFields")
    country = st.text_input("Country (optional — leave blank for Any)", "")
    require_country = st.checkbox("Require site in selected country", value=False)
    keywords = st.text_input("Extra keywords (comma-sep)", "immunotherapy,vaccine,device")
    status_ok = ["RECRUITING", "NOT_YET_RECRUITING"]
    if st.button("Refresh data"):
        st.cache_data.clear()
        st.toast("Data cache cleared. Re-running…", icon="♻️")

# Build a CT.gov v2 search expression from diagnosis+keywords
_DEF_TERMS = {
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

def build_terms(diagnosis: str, keywords: str):
    terms = []
    if diagnosis in _DEF_TERMS:
        terms.extend(_DEF_TERMS[diagnosis])
    else:
        terms.extend(["brain tumor", "spinal cord tumor", "CNS tumor"])
    extra = [k.strip() for k in (keywords or "").split(",") if k.strip()]
    # Prefer diagnosis terms primarily; keywords are applied in scoring and title/criteria matching
    return terms + extra

# v2 API search with pagination and caching (single term)
@st.cache_data(show_spinner=False, ttl=3600)
def ctgov_search_one(term: str, statuses, page_size: int = 100, max_pages: int = 5):
    base = "https://clinicaltrials.gov/api/v2/studies"
    session = requests.Session()
    session.headers.update({"User-Agent": "BrainTrialsFinder/1.0 (+https://clinicaltrials.gov)"})
    all_studies = []
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
        r = session.get(base, params=params, timeout=30)
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

@st.cache_data(show_spinner=False, ttl=3600)
def fetch_all_terms(terms, statuses, page_size=100, max_pages=5):
    dedup = {}
    for t in terms:
        try:
            for s in ctgov_search_one(t, statuses, page_size=page_size, max_pages=max_pages):
                ident = (s.get("protocolSection", {}) or {}).get("identificationModule", {}) or {}
                nct = ident.get("nctId")
                key = nct or id(s)
                # keep first occurrence
                if key not in dedup:
                    dedup[key] = s
        except requests.HTTPError:
            # Skip failing term silently; others may still succeed
            continue
    return list(dedup.values())

def km(lat1, lon1, lat2, lon2):
    if None in [lat1, lon1, lat2, lon2]: return None
    R=6371; dlat=math.radians(lat2-lat1); dlon=math.radians(lon2-lon1)
    a=math.sin(dlat/2)**2 + math.cos(math.radians(lat1))*math.cos(math.radians(lat2))*math.sin(dlon/2)**2
    return R*2*math.atan2(math.sqrt(a), math.sqrt(1-a))

# helpers

def mentions(txt, term):
    return bool(re.search(rf"\b{re.escape(term)}\b", txt or "", re.I))

def as_text(obj) -> str:
    # Convert eligibilityCriteria or other fields to a string safely
    if obj is None:
        return ""
    if isinstance(obj, dict):
        # v2 may use 'textblock' or 'textBlock' or direct 'value'
        for k in ("textblock", "textBlock", "value"):
            if k in obj:
                return str(obj.get(k) or "")
        # fallback to joining dict values
        return " ".join(str(v) for v in obj.values() if v is not None)
    if isinstance(obj, list):
        return "; ".join(as_text(x) for x in obj)
    return str(obj)

def parse_age_to_int(v):
    # Accept dicts with value, plain strings like '18 Years', or numbers
    if v is None:
        return None
    if isinstance(v, dict):
        return parse_age_to_int(v.get("value"))
    if isinstance(v, (int, float)):
        return int(v)
    s = str(v)
    m = re.search(r"(\d+)", s)
    if m:
        try:
            return int(m.group(1))
        except Exception:
            return None
    return None

def ensure_list(v):
    if v is None:
        return []
    if isinstance(v, list):
        return v
    return [v]


def score_trial(t, intake):
    # Use intake safely instead of globals
    age_local = (intake or {}).get("age")
    kps_local = (intake or {}).get("kps")
    prior_bev_local = bool((intake or {}).get("prior_bev", False))
    setting_local = (intake or {}).get("setting") or ""
    keywords_local = (intake or {}).get("keywords") or ""
    diagnosis_local = (intake or {}).get("diagnosis") or ""

    # derive diagnosis terms to match
    diag_terms = []
    if diagnosis_local in _DEF_TERMS:
        diag_terms = _DEF_TERMS[diagnosis_local]
    elif diagnosis_local and diagnosis_local != "Other":
        diag_terms = [diagnosis_local]
    else:
        diag_terms = ["brain tumor", "CNS tumor", "spinal cord tumor"]

    ps = (t or {}).get("protocolSection") or {}
    # Eligibility may be dict, string, or missing
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
    # Normalize phases/conditions
    phases_list = ensure_list(ps.get("designModule", {}).get("phases"))
    phases_up = [str(p).upper() for p in phases_list]
    conds_list = ensure_list(ps.get("conditionsModule", {}).get("conditions"))
    title = (ps.get("identificationModule", {}) or {}).get("briefTitle", "")

    # base score
    s = 0
    reasons = []
    # Diagnosis alignment (conditions/title contains any of the selected diagnosis terms)
    if any(any(mentions(c, term) for term in diag_terms) for c in conds_list) or any(mentions(title, term) for term in diag_terms):
        s += 30
        reasons.append(f"Matches diagnosis: {diagnosis_local or 'neuro-oncology'}.")
    # phases heuristic
    if any("PHASE 2" in p or "PHASE2" in p for p in phases_up):
        s += 8
    if any("PHASE 3" in p or "PHASE3" in p for p in phases_up):
        s += 12
    # age checks
    try:
        if min_age is not None and age_local is not None and age_local < min_age:
            reasons.append(f"Age below minimum ({min_age}).")
            s -= 30
        if max_age is not None and age_local is not None and age_local > max_age:
            reasons.append(f"Age above maximum ({max_age}).")
            s -= 30
    except Exception:
        pass
    # ECOG/KPS (heuristic)
    if mentions(crit, "ECOG 0-1") and (kps_local is None or kps_local < 80):
        s -= 15
        reasons.append("Requires ECOG 0–1 (KPS ~≥80).")
    if mentions(crit, "Karnofsky") and (kps_local is None or kps_local < 70):
        s -= 10
        reasons.append("Requires KPS ≥70.")
    # prior bev exclusion
    if prior_bev_local and mentions(crit, "no prior bevacizumab"):
        s -= 25
        reasons.append("Excludes prior bevacizumab.")
    # line of therapy alignment
    if setting_local == "Recurrent" and mentions(crit, "recurrent"):
        s += 8
    if setting_local == "Newly diagnosed" and (mentions(crit, "newly diagnosed") or mentions(title, "adjuvant")):
        s += 8
    # bonus for keyword hits
    for kw in [k.strip() for k in (keywords_local or "").split(",") if k.strip()]:
        if mentions(title, kw) or mentions(crit, kw):
            s += 3

    return max(0, min(100, s)), reasons

st.subheader("Results")
expr = build_terms(diagnosis, keywords)
with st.spinner("Fetching trials from ClinicalTrials.gov…"):
    try:
        studies = fetch_all_terms(expr, status_ok, page_size=100, max_pages=5)
    except requests.HTTPError as e:
        st.error("ClinicalTrials.gov API error. Try again later or simplify your query.")
        st.exception(e)
        studies = []
    except Exception as e:
        st.error("Unexpected error while fetching data.")
        st.exception(e)
        studies = []

rows = []
skipped = 0
for s in studies:
    try:
        locs = ((s.get("protocolSection", {}) or {}).get("contactsLocationsModule", {}) or {}).get("locations") or []
        # Optional country filter (case-insensitive substring match to be forgiving)
        if country and require_country:
            locs = [L for L in locs if country.lower() in (L.get("locationCountry") or "").lower()]
        if require_country and not locs:
            continue
        sc, reasons = score_trial(
            s,
            dict(age=age, kps=kps, prior_bev=prior_bev, setting=setting, keywords=keywords, diagnosis=diagnosis),
        )
        ident = (s.get("protocolSection", {}) or {}).get("identificationModule", {}) or {}
        title = ident.get("briefTitle", "")
        nct = ident.get("nctId")
        status = ((s.get("protocolSection", {}) or {}).get("statusModule", {}) or {}).get("overallStatus", "")
        phases_list = ensure_list(((s.get("protocolSection", {}) or {}).get("designModule", {}) or {}).get("phases")) or []
        phases = ", ".join(map(str, phases_list))
        conds_list = ensure_list(((s.get("protocolSection", {}) or {}).get("conditionsModule", {}) or {}).get("conditions")) or []
        conds = ", ".join(map(str, conds_list))
        first_site = next(iter(locs), {})
        site_str = f"{first_site.get('locationFacility','')}, {first_site.get('locationCity','')}, {first_site.get('locationCountry','')}"
        rows.append((sc, title, nct, status, phases, conds, site_str, "; ".join(reasons)))
    except Exception:
        skipped += 1
        continue

st.caption(f"Fetched {len(studies)} trials; showing {len(rows)} after filters.")
if skipped:
    st.caption(f"Skipped {skipped} record(s) due to unusual data format from API.")

if not rows:
    st.warning("No matching trials with the current filters. Try clearing Country (Any), changing Diagnosis/keywords, or expanding statuses.")
else:
    rows = sorted(rows, key=lambda x: -x[0])[:50]
    for sc, title, nct, status, phases, conds, site, reasons in rows:
        # If your Streamlit version doesn't support border, remove it.
        try:
            with st.container(border=True):
                if nct:
                    st.markdown(f"**[{title}](https://clinicaltrials.gov/study/{nct})**")
                else:
                    st.markdown(f"**{title}**")
                st.write(f"Status: {status} · Phases: {phases} · Site example: {site}")
                st.progress(sc/100)
                st.caption("Match reasons: " + (reasons or "—"))
        except TypeError:
            # fallback for older Streamlit versions
            with st.container():
                if nct:
                    st.markdown(f"**[{title}](https://clinicaltrials.gov/study/{nct})**")
                else:
                    st.markdown(f"**{title}**")
                st.write(f"Status: {status} · Phases: {phases} · Site example: {site}")
                st.progress(sc/100)
                st.caption("Match reasons: " + (reasons or "—"))

st.info("Sources: ClinicalTrials.gov v2 API. This is assistive information only; discuss with your clinician.")
