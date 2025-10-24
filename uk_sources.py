# UK sources aggregator (initial: ClinicalTrials.gov UK filter)
from typing import List, Dict, Any, Tuple

from ctgov_client import (
    build_terms,
    fetch_all_terms,
    score_trial,
    extract_row,
)

STATUSES = ["RECRUITING", "NOT_YET_RECRUITING"]


def _normalize_key(row: Dict[str, Any]) -> str:
    # Prefer identifiers; fallback to normalized title
    nct = (row.get("nct") or "").strip()
    if nct:
        return f"NCT:{nct}"
    title = (row.get("title") or "").lower().strip()
    return f"TITLE:{title}"


def fetch_uk_trials(
    diagnosis: str,
    keywords: str,
    intake: Dict[str, Any],
    include_ctgov: bool = True,
) -> Tuple[List[Dict[str, Any]], int, int]:
    """
    Fetch UK trials across selected sources.
    Currently implemented: ClinicalTrials.gov with UK site filter.

    Returns: (rows, total_raw, skipped)
    rows: list of standard rows with keys: title, nct, status, phases, conditions, site, score, reasons, url
    total_raw: number of raw studies fetched before filters
    skipped: number of studies skipped due to formatting issues
    """
    terms = build_terms(diagnosis, keywords)
    rows: List[Dict[str, Any]] = []
    skipped = 0
    total_raw = 0

    if include_ctgov:
        studies = fetch_all_terms(terms, STATUSES, page_size=100, max_pages=5)
        total_raw += len(studies)
        for s in studies:
            try:
                ps = (s.get("protocolSection", {}) or {})
                locs = ((ps.get("contactsLocationsModule", {}) or {}).get("locations") or [])
                # UK filter (case-insensitive contains)
                uk_locs = [L for L in locs if "united kingdom" in (L.get("locationCountry") or "").lower()]
                if not uk_locs:
                    continue
                sc, reasons = score_trial(s, intake)
                base = extract_row(s)
                # Replace site with first UK site
                first_site = next(iter(uk_locs), {})
                base["site"] = f"{first_site.get('locationFacility','')}, {first_site.get('locationCity','')}, {first_site.get('locationCountry','')}"
                base["score"] = sc
                base["reasons"] = "; ".join(reasons)
                base["url"] = f"https://clinicaltrials.gov/study/{base['nct']}" if base.get("nct") else ""
                rows.append(base)
            except Exception:
                skipped += 1
                continue

    # Deduplicate
    seen = set()
    deduped: List[Dict[str, Any]] = []
    for r in rows:
        k = _normalize_key(r)
        if k in seen:
            continue
        seen.add(k)
        deduped.append(r)

    # Sort
    deduped.sort(key=lambda x: -x.get("score", 0))
    return deduped, total_raw, skipped

