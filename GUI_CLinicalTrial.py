# GUI_CLinicalTrial.py — the Streamlit app body.
# Run with: streamlit run streamlit_app.py
#
# Searches ClinicalTrials.gov only. The web page (docs/index.html) also covers
# ISRCTN, EU CTIS and the national registries in WHO ICTRP.
import csv
import io

import streamlit as st

from ctgov_client import (
    DIAGNOSES,
    CTGovError,
    as_text,
    ensure_list,
    format_site,
    rank,
    search,
    study_locations,
)

st.set_page_config(page_title="Brain Trials Finder", layout="wide")

STATUSES = ["RECRUITING", "NOT_YET_RECRUITING"]
WEB_PAGE = "https://prazg.github.io/brain_trials_finder/"
COUNTRIES = [
    "Anywhere", "United Kingdom", "United States", "Australia", "Canada", "China", "France",
    "Germany", "India", "Italy", "Japan", "Netherlands", "Spain", "Other (type below)",
]
MAX_SHOWN = 100


@st.cache_data(ttl=3600, show_spinner=False)
def cached_search(diagnosis: str, keywords: str, country: str):
    """One hour's cache per distinct search. Failures are not cached."""
    return search(diagnosis, keywords, country=country, statuses=STATUSES, page_size=100, max_pages=5)


def _csv_safe(value):
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


def to_csv(rows) -> bytes:
    cols = ["score", "title", "nct", "url", "status", "phases", "conditions", "sponsor",
            "site", "countries", "reasons"]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=cols)
    writer.writeheader()
    for r in rows:
        writer.writerow({c: _csv_safe(r.get(c, "")) for c in cols})
    return buf.getvalue().encode("utf-8-sig")


def md_escape(text: str) -> str:
    """Register text goes into markdown; keep its brackets and stars literal."""
    out = str(text or "")
    for ch in "\\[]*_`<>#|":
        out = out.replace(ch, "\\" + ch)
    return out


st.title("Brain Cancer Trials Finder")
st.caption(
    "Recruiting neuro-oncology trials from ClinicalTrials.gov, ranked against a patient's "
    "details. **This is a search aid, not medical advice** — eligibility is decided by the "
    f"trial team. For UK, EU and other national registers as well, use the [web page]({WEB_PAGE})."
)

with st.sidebar:
    with st.form("search_form"):
        st.subheader("Search")
        diagnosis = st.selectbox("Diagnosis", list(DIAGNOSES.keys()), index=0)
        keywords = st.text_input(
            "Must also mention (optional)",
            value="",
            placeholder="e.g. TTFields, CAR-T, MGMT",
            help="Comma separated. Narrows the results to trials mentioning at least one of these.",
        )
        country_choice = st.selectbox("Country with a trial site", COUNTRIES, index=1)
        country_other = st.text_input("Other country", value="", placeholder="e.g. Ireland")

        st.subheader("Patient (used only to rank)")
        age = st.number_input("Age (years)", min_value=0, max_value=120, value=55, step=1)
        kps = st.slider("Karnofsky (KPS)", min_value=0, max_value=100, step=10, value=80)
        setting = st.selectbox("Disease setting", ["Recurrent", "Newly diagnosed", "Not specified"], index=0)
        prior_bev = st.checkbox("Has had bevacizumab", value=False)
        show_unrelated = st.checkbox(
            "Include records that look unrelated",
            value=False,
            help="'GBM' also abbreviates glomerular basement membrane, so a few kidney "
                 "trials match the search. They are hidden unless this is ticked.",
        )
        submitted = st.form_submit_button("Search", type="primary")

# Results are kept in the session so that touching any widget does not blank
# the page (the earlier version only drew results on the run where the button
# was pressed).
if submitted or "active_query" not in st.session_state:
    if country_choice == "Anywhere":
        country = ""
    elif country_choice.startswith("Other"):
        country = country_other.strip()
    else:
        country = country_choice
    st.session_state["active_query"] = {
        "diagnosis": diagnosis,
        "keywords": keywords.strip(),
        "country": country,
        "age": int(age),
        "kps": int(kps),
        "setting": "" if setting == "Not specified" else setting,
        "prior_bev": bool(prior_bev),
        "show_unrelated": bool(show_unrelated),
    }

q = st.session_state["active_query"]

try:
    with st.spinner("Searching ClinicalTrials.gov…"):
        studies, total = cached_search(q["diagnosis"], q["keywords"], q["country"])
except CTGovError as exc:
    st.error(f"{exc}")
    st.info(f"Nothing was changed. Press **Search** to try again, or use the [web page]({WEB_PAGE}).")
    st.stop()

rows, hidden = rank(studies, q, include_off_topic=q["show_unrelated"])

where = q["country"] or "anywhere"
summary = (
    f"**{len(rows)}** trial{'' if len(rows) == 1 else 's'} for **{q['diagnosis']}**, sites in **{where}**"
    + (f", mentioning *{q['keywords']}*" if q["keywords"] else "")
    + "."
)
if total > len(studies):
    summary += f" The register holds {total}; the first {len(studies)} were fetched."
if hidden:
    summary += f" {hidden} hidden because they look unrelated to brain or spine tumours."
st.markdown(summary)

if not rows:
    st.warning(
        "Nothing matched. Try removing the keywords, choosing “Anywhere”, or a broader diagnosis "
        "such as “Any CNS tumour”."
    )
    st.stop()

st.download_button(
    "Download these results (CSV)",
    data=to_csv(rows),
    file_name=f"brain-trials-{q['diagnosis'].lower().replace(' ', '-')}.csv",
    mime="text/csv",
)
if len(rows) > MAX_SHOWN:
    st.caption(f"Showing the top {MAX_SHOWN} by match score. The CSV contains all {len(rows)}.")

for row in rows[:MAX_SHOWN]:
    study = row["study"]
    with st.container(border=True):
        title = md_escape(row["title"]) or row["nct"]
        st.markdown(f"**[{title}]({row['url']})**" if row["url"] else f"**{title}**")
        meta = [row["nct"] or "—", row["status"] or "—"]
        if row["phases"]:
            meta.append(row["phases"])
        if row["sponsor"]:
            meta.append(md_escape(row["sponsor"]))
        st.markdown(" · ".join(meta))
        st.progress(row["score"] / 100, text=f"Match score {row['score']} — {md_escape(row['reasons']) or 'no adjustments'}")
        if row["conditions"]:
            st.caption(f"Conditions: {md_escape(row['conditions'])}")

        preferred = study_locations(study, q["country"]) if q["country"] else []
        locations = preferred or study_locations(study)
        label = f"{len(locations)} site{'' if len(locations) == 1 else 's'}"
        if preferred:
            label += f" in {q['country']}"
        with st.expander(label + " and contacts"):
            clm = (study.get("protocolSection") or {}).get("contactsLocationsModule") or {}
            centrals = ensure_list(clm.get("centralContacts"))
            if centrals:
                st.markdown("**Central contacts**")
                for c in centrals:
                    parts = [c.get("name"), c.get("phone"), c.get("email")]
                    st.text(" | ".join(p for p in parts if p))
            if locations:
                st.markdown("**Sites**")
                for loc in locations[:60]:
                    line = format_site(loc) or "Location not published"
                    if loc["status"] and loc["status"] != "RECRUITING":
                        line += f" ({loc['status'].replace('_', ' ').lower()})"
                    st.text(line)
                if len(locations) > 60:
                    st.caption(f"…and {len(locations) - 60} more on ClinicalTrials.gov.")
            elif not centrals:
                st.caption("No contacts or sites published for this trial yet.")

        elig = (study.get("protocolSection") or {}).get("eligibilityModule") or {}
        criteria = as_text(elig.get("eligibilityCriteria"))
        if criteria:
            with st.expander("Eligibility criteria as published"):
                ages = " to ".join(a for a in (elig.get("minimumAge"), elig.get("maximumAge")) if a)
                if ages:
                    st.caption(f"Ages: {ages}")
                st.text(criteria[:6000] + ("…" if len(criteria) > 6000 else ""))
