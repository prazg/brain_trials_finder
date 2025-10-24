# python
# GUI_CLinicalTrial.py — run with: streamlit run GUI_CLinicalTrial.py
import re
import requests
import streamlit as st

st.set_page_config(page_title="Brain Trials Finder", layout="wide")

STATUSES = ["RECRUITING", "NOT_YET_RECRUITING"]

DEFAULT_DIAG_TERMS = {
    "Glioblastoma": ["glioblastoma", "GBM", "grade 4 astrocytoma"],
    "Diffuse midline glioma": ["diffuse midline glioma", "DMG"],
    "Anaplastic astrocytoma": ["anaplastic astrocytoma", "grade 3 astrocytoma"],
}


def ensure_list(v):
    if isinstance(v, list):
        return v
    if v is None:
        return []
    return [v]


def mentions(text: str, needle: str) -> bool:
    if not text:
        return False
    return needle.lower() in text.lower()


def _to_int(v):
    try:
        if v is None:
            return None
        if isinstance(v, (int, float)):
            return int(v)
        # Extract first integer from strings like "18 Years"
        m = re.search(r"(\d+)", str(v))
        return int(m.group(1)) if m else None
    except Exception:
        return None


def build_terms(diagnosis: str, keywords: str):
    base = DEFAULT_DIAG_TERMS.get(diagnosis, [])
    extra = [k.strip() for k in (keywords or "").split(",") if k.strip()]
    terms = list(dict.fromkeys([*base, *extra]))  # de-duplicate preserve order
    return terms or ["brain tumor"]


def build_expr(diagnosis: str, keywords: str) -> str:
    terms = build_terms(diagnosis, keywords)
    # Simple OR query; v2 tokenizes internally
    return " OR ".join(f'"{t}"' if " " in t else t for t in terms)


@st.cache_data(ttl=3600)
def ctgov_search(expr: str, statuses, page_size: int = 100, max_pages: int = 5):
    """Return a list of study dicts from ClinicalTrials.gov v2."""
    url = "https://clinicaltrials.gov/api/v2/studies"
    all_studies = []
    token = None
    for _ in range(max_pages):
        params = {
            "query.term": expr,
            "pageSize": page_size,
            "filter.overallStatus": ",".join(statuses),
        }
        if token:
            params["pageToken"] = token
        r = requests.get(url, params=params, timeout=30)
        r.raise_for_status()
        data = r.json() or {}
        studies = data.get("studies") or []
        all_studies.extend(studies)
        token = data.get("nextPageToken")
        if not token:
            break
    return all_studies


def extract_row(study: dict) -> dict:
    ps = (study.get("protocolSection") or {})
    idm = (ps.get("identificationModule") or {})
    scm = (ps.get("statusModule") or {})
    dsm = (ps.get("designModule") or {})
    cdnm = (ps.get("conditionsModule") or {})
    slm = (ps.get("sponsorCollaboratorsModule") or {})

    title = (idm.get("officialTitle") or idm.get("briefTitle") or "").strip()
    nct = (idm.get("nctId") or "").strip()

    status_raw = (scm.get("overallStatus") or "").strip()
    status = status_raw.replace("_", " ").title() if status_raw else ""

    phases_list = ensure_list(dsm.get("phases"))
    # Pretty print phases like "PHASE2" -> "Phase 2"
    def fmt_phase(p: str) -> str:
        p = str(p or "").upper()
        if p.startswith("PHASE"):
            pnum = p.replace("PHASE", "").replace("_", "/").strip()
            pnum = pnum.replace("1/2", "1/2").replace("2/3", "2/3")
            return f"Phase {pnum}" if pnum else "Phase"
        return p.title() if p else ""
    phases = ", ".join([fmt_phase(p) for p in phases_list if p])

    conditions = ", ".join(ensure_list(cdnm.get("conditions")))

    sponsor = ""
    lead = slm.get("leadSponsor") or {}
    if isinstance(lead, dict):
        sponsor = (lead.get("name") or "").strip()

    return {
        "title": title,
        "nct": nct,
        "status": status,
        "phases": phases,
        "conditions": conditions,
        "sponsor": sponsor,
    }


def score_trial(study: dict, intake: dict):
    ps = (study.get("protocolSection") or {})
    scm = (ps.get("statusModule") or {})
    dsm = (ps.get("designModule") or {})
    elm = (ps.get("eligibilityModule") or {})
    idm = (ps.get("identificationModule") or {})

    s = 0
    reasons = []

    status = (scm.get("overallStatus") or "")
    if status == "RECRUITING":
        s += 15
    elif status == "NOT_YET_RECRUITING":
        s += 8

    phases = ensure_list(dsm.get("phases"))
    if any("PHASE3" in str(p).upper() for p in phases):
        s += 12
    if any("PHASE2" in str(p).upper() for p in phases):
        s += 8

    # Age checks
    min_age_raw = elm.get("minimumAge")
    max_age_raw = elm.get("maximumAge")
    min_age = _to_int(min_age_raw)
    max_age = _to_int(max_age_raw)
    age = int(intake.get("age") or 0)
    if min_age is not None and age < min_age:
        reasons.append(f"Age below minimum ({min_age_raw}).")
        s -= 30
    if max_age is not None and age > max_age:
        reasons.append(f"Age above maximum ({max_age_raw}).")
        s -= 30

    # KPS heuristic from criteria text
    crit = elm.get("eligibilityCriteria") or ""
    kps = int(intake.get("kps") or 0)
    if mentions(crit, "Karnofsky") and kps < 70:
        s -= 10
        reasons.append("Requires KPS ≥70.")

    # Keyword bonus
    title = (idm.get("briefTitle") or idm.get("officialTitle") or "")
    summary = (ps.get("descriptionModule", {}) or {}).get("briefSummary") or ""
    keywords = [k.strip() for k in (intake.get("keywords") or "").split(",") if k.strip()]
    blob = " ".join([title, summary])
    for kw in keywords:
        if mentions(blob, kw):
            s += 2

    return s, reasons


# UI
st.title("Brain Cancer Trials Finder (MVP)")

with st.sidebar:
    diagnosis = st.selectbox(
        "Diagnosis",
        ["Glioblastoma", "Diffuse midline glioma", "Anaplastic astrocytoma", "Other"],
        index=0,
    )
    setting = st.selectbox("Setting", ["Newly diagnosed", "Recurrent"], index=1)
    age = st.number_input("Age", min_value=1, max_value=100, value=55)
    kps = st.slider("Karnofsky (KPS)", min_value=40, max_value=100, step=10, value=80)
    prior_bev = st.checkbox("Prior bevacizumab", value=False)
    keywords = st.text_input("Keywords (comma-separated)", value="immunotherapy,vaccine,device")
    do_search = st.button("Search", type="primary")

# Trigger search on first load too
if do_search or "did_first" not in st.session_state:
    st.session_state["did_first"] = True
    expr = build_expr(diagnosis, keywords)
    studies = ctgov_search(expr, STATUSES, page_size=100, max_pages=5)

    intake = {
        "age": age,
        "kps": kps,
        "prior_bev": prior_bev,
        "setting": setting,
        "keywords": keywords,
        "diagnosis": diagnosis,
    }

    rows = []
    for sdict in studies:
        try:
            sc, reasons = score_trial(sdict, intake)
            row = extract_row(sdict)
            nct = row.get("nct") or ""
            url = f"https://clinicaltrials.gov/study/{nct}" if nct else ""
            rows.append(
                (
                    sc,
                    row.get("title", ""),
                    nct,
                    row.get("status", ""),
                    row.get("phases", ""),
                    row.get("conditions", ""),
                    row.get("sponsor", ""),
                    reasons,
                    url,
                    sdict,
                )
            )
        except Exception:
            continue

    rows = sorted(rows, key=lambda x: -x[0])[:50]

    st.caption(f"Found {len(studies)} studies; showing top {len(rows)} by score.")

    for sc, title, nct, status, phases, conds, sponsor, reasons, url, study in rows:
        with st.container(border=True):
            if url:
                st.markdown(f"**[{title}]({url})**")
            else:
                st.markdown(f"**{title}**")
            meta = f"NCT: {nct or '—'} · Sponsor: {sponsor or '—'} · Status: {status or '—'} · Phases: {phases or '—'} · Score: {sc}"
            st.write(meta)
            if conds:
                st.write(f"Conditions: {conds}")

            with st.expander("Contacts and Locations"):
                ps = (study.get("protocolSection") or {})
                clm = (ps.get("contactsLocationsModule") or {})

                centrals = ensure_list(clm.get("centralContacts"))
                if centrals:
                    st.write("Central Contacts:")
                    for c in centrals:
                        parts = [c.get("name"), c.get("role"), c.get("phone"), c.get("email")]
                        st.write(" - " + " | ".join([p for p in parts if p]))

                officials = ensure_list(clm.get("overallOfficials"))
                if officials:
                    st.write("Overall Officials:")
                    for o in officials:
                        parts = [o.get("name"), o.get("role"), o.get("affiliation")]
                        st.write(" - " + " | ".join([p for p in parts if p]))

                locs = ensure_list(clm.get("locations"))
                if locs:
                    st.write("Locations:")
                    for L in locs:
                        facility = (L.get("locationFacility") or "").strip()
                        city = (L.get("locationCity") or "").strip()
                        state = (L.get("locationState") or "").strip()
                        country = (L.get("locationCountry") or "").strip()
                        status_l = (L.get("status") or "").strip()
                        site_line = ", ".join([p for p in [facility, city, state, country] if p])
                        if site_line:
                            st.write(f" - {site_line}" + (f" (status: {status_l})" if status_l else ""))
                        lcontacts = ensure_list(L.get("contacts")) or ensure_list(L.get("locationContacts"))
                        for lc in lcontacts:
                            parts = [lc.get("name"), lc.get("role"), lc.get("phone"), lc.get("email")]
                            parts = [p for p in parts if p]
                            if parts:
                                st.write("    • " + " | ".join(parts))

            if reasons:
                with st.expander("Why this score?"):
                    for r in reasons:
                        st.write(f"- {r}")
