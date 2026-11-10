"""UK trials: ClinicalTrials.gov studies with at least one United Kingdom site."""
from typing import Any, Dict, List, Tuple

from ctgov_client import rank, search

STATUSES = ["RECRUITING", "NOT_YET_RECRUITING"]
UK = "United Kingdom"


def fetch_uk_trials(
    diagnosis: str,
    keywords: str,
    intake: Dict[str, Any],
    include_ctgov: bool = True,
) -> Tuple[List[Dict[str, Any]], int, int]:
    """
    Returns (rows, total_on_register, hidden_as_unrelated).

    rows carry: title, nct, url, status, phases, conditions, sponsor, site,
    city_country, countries, score, reasons, study.

    The UK filter runs on the server (query.locn). Filtering client-side meant
    trimming the first few pages of a worldwide result set, which misses UK
    trials further down it.
    Raises ctgov_client.CTGovError if the register cannot be read.
    """
    if not include_ctgov:
        return [], 0, 0
    studies, total = search(diagnosis, keywords, country=UK, statuses=STATUSES,
                            page_size=100, max_pages=5)
    rows, hidden = rank(studies, {**(intake or {}), "diagnosis": diagnosis,
                                  "keywords": keywords, "country": UK})
    return rows, total, hidden
