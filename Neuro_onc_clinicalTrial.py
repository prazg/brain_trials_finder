#!/usr/bin/env python3
import argparse
import csv
import json
from typing import List, Dict, Any

from ctgov_client import (
    DEFAULT_DIAG_TERMS,
    build_terms,
    fetch_all_terms,
    score_trial,
    extract_row,
)

STATUSES = ["RECRUITING", "NOT_YET_RECRUITING"]


def save_results(rows: List[Dict[str, Any]], csv_path: str, json_path: str):
    if not rows:
        print("No studies found.")
        return
    # stable header order
    keys = [
        "score",
        "title",
        "nct",
        "url",
        "status",
        "phases",
        "conditions",
        "site",
        "reasons",
    ]
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=keys)
        writer.writeheader()
        for r in rows:
            writer.writerow({k: r.get(k, "") for k in keys})
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2, ensure_ascii=False)
    print(f"Wrote {len(rows)} studies to {csv_path} and {json_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Download actively recruiting neuro-oncology trials from ClinicalTrials.gov v2 API (robust client)"
    )
    parser.add_argument(
        "--diagnosis",
        default="Glioblastoma",
        choices=list(DEFAULT_DIAG_TERMS.keys()) + ["Other"],
        help="Primary diagnosis category to search for.",
    )
    parser.add_argument(
        "--keywords",
        default="",
        help="Extra keywords (comma-separated) to refine search.",
    )
    parser.add_argument("--age", type=int, default=55, help="Patient age (years)")
    parser.add_argument("--kps", type=int, default=80, help="Karnofsky Performance Status (40-100)")
    parser.add_argument("--prior-bev", action="store_true", help="Indicate prior bevacizumab exposure")
    parser.add_argument(
        "--setting",
        default="Recurrent",
        choices=["Newly diagnosed", "Recurrent"],
        help="Disease setting",
    )
    parser.add_argument("--country", default="", help="Filter: require location country containing this text (case-insensitive)")
    parser.add_argument("--require-country", action="store_true", help="If set, require at least one site in the given country text")
    parser.add_argument("--csv", default="neuro_onc_trials.csv", help="CSV output path")
    parser.add_argument("--json", default="neuro_onc_trials.json", help="JSON output path")
    parser.add_argument("--page-size", type=int, default=100, help="Results per page per term (max 1000)")
    parser.add_argument("--pages", type=int, default=5, help="Max pages to fetch per term")
    args = parser.parse_args()

    terms = build_terms(args.diagnosis, args.keywords)
    print("Searching ClinicalTrials.gov for:")
    print("  Diagnosis:", args.diagnosis)
    if args.keywords:
        print("  Extra keywords:", args.keywords)

    studies = fetch_all_terms(terms, STATUSES, page_size=args.page_size, max_pages=args.pages)

    rows: List[Dict[str, Any]] = []
    skipped = 0
    for s in studies:
        try:
            ps = (s.get("protocolSection", {}) or {})
            locs = ((ps.get("contactsLocationsModule", {}) or {}).get("locations") or [])
            if args.country and args.require_country:
                locs = [L for L in locs if args.country.lower() in (L.get("locationCountry") or "").lower()]
            if args.require_country and not locs:
                continue
            sc, reasons = score_trial(
                s,
                dict(
                    age=args.age,
                    kps=args.kps,
                    prior_bev=args.prior_bev,
                    setting=args.setting,
                    keywords=args.keywords,
                    diagnosis=args.diagnosis,
                ),
            )
            base = extract_row(s)
            base["score"] = sc
            base["reasons"] = "; ".join(reasons)
            base["url"] = f"https://clinicaltrials.gov/study/{base['nct']}" if base.get("nct") else ""
            rows.append(base)
        except Exception:
            skipped += 1
            continue

    rows.sort(key=lambda x: -x.get("score", 0))
    print(f"Fetched {len(studies)} trials; showing {len(rows)} after filters. Skipped {skipped}.")

    save_results(rows, args.csv, args.json)


if __name__ == "__main__":
    main()