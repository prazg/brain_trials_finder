#!/usr/bin/env python3
"""
Export recruiting neuro-oncology trials from ClinicalTrials.gov to CSV and JSON.

    python Neuro_onc_clinicalTrial.py --diagnosis Glioblastoma --country "United Kingdom"
    python Neuro_onc_clinicalTrial.py --diagnosis Meningioma --keywords "proton, radiosurgery"
"""
import argparse
import csv
import json
import sys
from typing import Any, Dict, List

from ctgov_client import DIAGNOSES, CTGovError, rank, search

STATUSES = ["RECRUITING", "NOT_YET_RECRUITING"]

COLUMNS = ["score", "title", "nct", "url", "status", "phases", "conditions",
           "sponsor", "site", "countries", "reasons"]


def _csv_safe(value: Any) -> Any:
    """Register text is untrusted: stop a title starting with = + - @ running as a formula."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


def save_results(rows: List[Dict[str, Any]], csv_path: str, json_path: str) -> None:
    if not rows:
        print("No studies found; nothing written.")
        return
    # utf-8-sig so Excel reads accented characters correctly
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        for r in rows:
            writer.writerow({k: _csv_safe(r.get(k, "")) for k in COLUMNS})
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump([{k: r.get(k, "") for k in COLUMNS} for r in rows], f, indent=2, ensure_ascii=False)
    print(f"Wrote {len(rows)} studies to {csv_path} and {json_path}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Download recruiting neuro-oncology trials from the ClinicalTrials.gov v2 API."
    )
    parser.add_argument("--diagnosis", default="Glioblastoma",
                        choices=list(DIAGNOSES.keys()) + ["Other"],
                        help="Diagnosis to search for ('Other' means any CNS tumour).")
    parser.add_argument("--keywords", default="",
                        help="Comma-separated words that narrow the search, e.g. 'TTFields, CAR-T'.")
    parser.add_argument("--age", type=int, default=55, help="Patient age in years")
    parser.add_argument("--kps", type=int, default=80, help="Karnofsky Performance Status (0-100)")
    parser.add_argument("--prior-bev", action="store_true", help="Patient has had bevacizumab")
    parser.add_argument("--setting", default="Recurrent", choices=["Newly diagnosed", "Recurrent"],
                        help="Disease setting")
    parser.add_argument("--country", default="",
                        help="Only trials with a site in this country, e.g. 'United Kingdom'")
    parser.add_argument("--require-country", action="store_true",
                        help="Kept for old scripts; --country now always filters.")
    parser.add_argument("--include-unrelated", action="store_true",
                        help="Keep records that match a term in an unrelated sense "
                             "(e.g. GBM as glomerular basement membrane).")
    parser.add_argument("--csv", default="neuro_onc_trials.csv", help="CSV output path")
    parser.add_argument("--json", default="neuro_onc_trials.json", help="JSON output path")
    parser.add_argument("--page-size", type=int, default=100, help="Results per page (max 1000)")
    parser.add_argument("--pages", type=int, default=5, help="Max pages to fetch")
    args = parser.parse_args()

    if not 0 <= args.age <= 120:
        parser.error("--age must be between 0 and 120")
    if not 0 <= args.kps <= 100:
        parser.error("--kps must be between 0 and 100")

    print("Searching ClinicalTrials.gov")
    print("  Diagnosis:", args.diagnosis)
    if args.keywords:
        print("  Must also mention:", args.keywords)
    if args.country:
        print("  Sites in:", args.country)

    try:
        studies, total = search(args.diagnosis, args.keywords, country=args.country,
                                statuses=STATUSES, page_size=args.page_size, max_pages=args.pages)
    except CTGovError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    rows, hidden = rank(
        studies,
        dict(age=args.age, kps=args.kps, prior_bev=args.prior_bev, setting=args.setting,
             keywords=args.keywords, diagnosis=args.diagnosis, country=args.country),
        include_off_topic=args.include_unrelated,
    )
    print(f"Register holds {total} matching trials; fetched {len(studies)}, kept {len(rows)}"
          + (f", hid {hidden} that look unrelated" if hidden else "") + ".")
    if total > len(studies):
        print(f"  Note: {total - len(studies)} more exist. Raise --pages to fetch them.")

    save_results(rows, args.csv, args.json)
    print("This is a search aid, not medical advice. Eligibility is decided by the trial team.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
