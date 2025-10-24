# app.py  —  streamlit run app.py
import requests, re, math
import streamlit as st

st.set_page_config(page_title="Brain Trials Finder", layout="wide")

st.title("Brain Cancer Trials Finder (MVP)")
with st.sidebar:
    age = st.number_input("Age", 1, 100, 55)
    diagnosis = st.selectbox("Diagnosis", ["Glioblastoma", "Diffuse midline glioma", "Anaplastic astrocytoma", "Other"])
    setting = st.selectbox("Disease setting", ["Newly diagnosed", "Recurrent"])
    kps = st.slider("Karnofsky (approx)", 40, 100, 80, 10)
    prior_bev = st.checkbox("Prior bevacizumab")
    prior_ttf = st.checkbox("Prior TTFields")
    country = st.text_input("Country (2-letter or name)", "United Kingdom")
    keywords = st.text_input("Extra keywords (comma-sep)", "immunotherapy,vaccine,device")
    status_ok = ["RECRUITING", "NOT_YET_RECRUITING"]

# python
def ctgov_search(condition, country, statuses, size=50, page_token=None):
    base = "https://clinicaltrials.gov/api/v2/studies"
    params = {
        "query.term": condition,
        "filter.overallStatus": ",".join(statuses),  # v2 expects overallStatus
        "pageSize": size,
    }
    if page_token:
        params["pageToken"] = page_token  # for pagination if you choose to use it

    session = requests.Session()
    session.headers.update({"User-Agent": "BrainTrialsFinder/1.0 (+https://clinicaltrials.gov)"})

    r = session.get(base, params=params, timeout=30)
    if r.status_code == 400:
        st.warning("ClinicalTrials.gov API rejected the request (400). Try simplifying the query.")
    r.raise_for_status()
    return r.json().get("studies", [])


def km(lat1, lon1, lat2, lon2):
    if None in [lat1, lon1, lat2, lon2]: return None
    R=6371; dlat=math.radians(lat2-lat1); dlon=math.radians(lon2-lon1)
    a=math.sin(dlat/2)**2 + math.cos(math.radians(lat1))*math.cos(math.radians(lat2))*math.sin(dlon/2)**2
    return R*2*math.atan2(math.sqrt(a), math.sqrt(1-a))

# naive mention helper
def mentions(txt, term):
    return bool(re.search(rf"\b{re.escape(term)}\b", txt or "", re.I))

def _to_int(v):
    try:
        return int(float(v))
    except Exception:
        return None

def score_trial(t, intake):
    elig = t.get("protocolSection", {}).get("eligibilityModule", {})
    crit = (elig.get("eligibilityCriteria") or {}).get("textblock", "") or ""
    phases = (t.get("protocolSection", {}).get("designModule", {}).get("phases") or [])
    conds = (t.get("protocolSection", {}).get("conditionsModule", {}).get("conditions") or [])
    title = t.get("protocolSection", {}).get("identificationModule", {}).get("briefTitle","")
    # base score
    s = 0; reasons=[]
    if any(mentions(c, "glioblastoma") for c in conds) or mentions(title, "glioblastoma"):
        s += 40; reasons.append("Condition matches glioblastoma/brain tumor.")
    if "Phase 2" in phases or "PHASE2" in phases: s += 8
    if "Phase 3" in phases or "PHASE3" in phases: s += 12
    # age (handle numeric or string)
    min_age_raw = elig.get("minimumAge", {}).get("value")
    max_age_raw = elig.get("maximumAge", {}).get("value")
    min_age = _to_int(min_age_raw)
    max_age = _to_int(max_age_raw)
    if min_age is not None and age < min_age:
        reasons.append(f"Age below minimum ({min_age_raw})."); s -= 30
    if max_age is not None and age > max_age:
        reasons.append(f"Age above maximum ({max_age_raw})."); s -= 30
    # ECOG/KPS (heuristic)
    if mentions(crit, "ECOG 0-1") and kps < 80: s -= 15; reasons.append("Requires ECOG 0–1 (KPS ~≥80).")
    if mentions(crit, "Karnofsky") and kps < 70: s -= 10; reasons.append("Requires KPS ≥70.")
    # prior bev exclusion
    if prior_bev and mentions(crit, "no prior bevacizumab"):
        s -= 25; reasons.append("Excludes prior bevacizumab.")
    # line of therapy alignment
    if setting == "Recurrent" and mentions(crit, "recurrent"): s += 8
    if setting == "Newly diagnosed" and (mentions(crit,"newly diagnosed") or mentions(title,"adjuvant")): s += 8
    # bonus for keyword hits
    for kw in [k.strip() for k in keywords.split(",") if k.strip()]:
        if mentions(title, kw) or mentions(crit, kw): s += 3
    return max(0,min(100,s)), reasons

st.subheader("Results")
studies = ctgov_search("brain tumor OR glioblastoma", country, status_ok)
rows = []
for s in studies:
    locs = (s.get("protocolSection", {}).get("contactsLocationsModule", {}).get("locations") or [])
    # rough country filter
    locs = [L for L in locs if (L.get("locationCountry") or "").lower().startswith(country.lower())]
    if not locs:
        continue
    sc, reasons = score_trial(s, dict(age=age, kps=kps))
    ident = s.get("protocolSection", {}).get("identificationModule", {})
    title = ident.get("briefTitle", "")
    nct = ident.get("nctId")
    status = s["protocolSection"]["statusModule"]["overallStatus"]
    phases = ", ".join(s.get("protocolSection", {}).get("designModule", {}).get("phases") or [])
    conds = ", ".join(s.get("protocolSection", {}).get("conditionsModule", {}).get("conditions") or [])
    first_site = next(iter(locs), {})
    site_str = f"{first_site.get('locationFacility','')}, {first_site.get('locationCity','')}, {first_site.get('locationCountry','')}"
    rows.append((sc, title, nct, status, phases, conds, site_str, "; ".join(reasons)))

rows = sorted(rows, key=lambda x: -x[0])[:50]
for sc, title, nct, status, phases, conds, site, reasons in rows:
    with st.container(border=True):
        if nct:
            st.markdown(f"**[{title}](https://clinicaltrials.gov/study/{nct})**")
        else:
            st.markdown(f"**{title}**")
        st.write(f"Status: {status} · Phases: {phases} · Site example: {site}")
        st.progress(sc/100)
        st.caption("Match reasons: " + (reasons or "—"))
st.info("Sources: ClinicalTrials.gov v2 API. This is assistive information only; discuss with your clinician.")
