"""
Offline tests for the Python side: the shared ClinicalTrials.gov client and
the two snapshot builders. No network.

    python -m unittest discover -s test -p "test_*.py" -v
"""
import json
import re
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import build_ictrp  # noqa: E402
import build_snapshot  # noqa: E402
import ctgov_client as c  # noqa: E402

FIXTURE = json.loads((ROOT / "test" / "fixtures" / "ctg_page1.json").read_text(encoding="utf-8"))
STUDIES = {c.nct_id(s): s for s in FIXTURE["studies"]}


def ctis_detail(events_by_country, periods=None):
    """Smallest CTIS detail record that the recruitment logic reads."""
    return {
        "events": {"trialEvents": [
            {"mscName": name, "events": [{"notificationType": k, "date": d} for k, d in evs]}
            for name, evs in events_by_country.items()]},
        "authorizedApplication": {"authorizedPartsII": [
            {"mscInfo": {"mscName": name, "activeTrialRecruitmentPeriod": period}}
            for name, period in (periods or {}).items()]},
    }


class ClientQuery(unittest.TestCase):
    def test_keywords_narrow_instead_of_widening(self):
        p = c.build_params(c.diagnosis_terms("Glioblastoma"), "immunotherapy, vaccine", "United Kingdom")
        self.assertEqual(p["query.cond"], 'glioblastoma OR GBM OR "glioblastoma multiforme" OR gliosarcoma')
        self.assertEqual(p["query.term"], "immunotherapy OR vaccine")
        self.assertEqual(p["query.locn"], 'AREA[LocationCountry]"United Kingdom"')
        self.assertNotIn("immunotherapy", p["query.cond"])

    def test_no_keywords_no_country(self):
        p = c.build_params(["meningioma"])
        self.assertNotIn("query.term", p)
        self.assertNotIn("query.locn", p)

    def test_build_terms_ignores_keywords(self):
        self.assertEqual(c.build_terms("Meningioma", "vaccine, device"), ["meningioma"])

    def test_legacy_diagnosis_names_still_resolve(self):
        self.assertEqual(c.diagnosis_terms("Other"), c.DIAGNOSES["Any CNS tumour"])
        self.assertEqual(c.diagnosis_terms("Spinal cord tumor"), c.DIAGNOSES["Spinal cord tumour"])
        self.assertEqual(c.diagnosis_terms("pineoblastoma"), ["pineoblastoma"])

    def test_split_keywords(self):
        self.assertEqual(c.split_keywords(" TTFields, CAR-T ,, ttfields "), ["TTFields", "CAR-T"])
        self.assertEqual(c.split_keywords(None), [])


class ClientRecords(unittest.TestCase):
    def test_locations_use_v2_field_names(self):
        locs = c.study_locations(STUDIES["NCT06630260"])
        self.assertTrue(locs)
        self.assertTrue(all(loc["country"] for loc in locs))
        self.assertTrue(any(loc["facility"] for loc in locs))
        uk = c.study_locations(STUDIES["NCT06630260"], "united kingdom")
        self.assertTrue(uk and all(loc["country"] == "United Kingdom" for loc in uk))

    def test_extract_row_prefers_a_site_in_the_chosen_country(self):
        row = c.extract_row(STUDIES["NCT04094610"], "United Kingdom")
        self.assertIn("United Kingdom", row["site"])
        self.assertEqual(row["url"], "https://clinicaltrials.gov/study/NCT04094610")
        self.assertEqual(row["phases"], "Phase 1/2")

    def test_ages(self):
        self.assertEqual(c.parse_age_to_int("18 Years"), 18)
        self.assertEqual(c.parse_age_to_int("6 Months"), 0)    # was read as 6 years
        self.assertEqual(c.parse_age_to_int("30 Months"), 2)
        self.assertEqual(c.parse_age_to_int("90 Days"), 0)
        self.assertIsNone(c.parse_age_to_int(None))
        self.assertIsNone(c.parse_age_to_int("N/A"))

    def test_phase_formatting(self):
        self.assertEqual(c.format_phases(["PHASE1", "PHASE2"]), "Phase 1/2")
        self.assertEqual(c.format_phases(["EARLY_PHASE1"]), "Early phase 1")
        self.assertEqual(c.format_phases(["NA"]), "")
        self.assertEqual(c.format_phases(None), "")

    def test_anti_gbm_disease_is_off_topic(self):
        self.assertTrue(c.is_off_topic(STUDIES["NCT07018024"]))
        self.assertFalse(c.is_off_topic(STUDIES["NCT06630260"]))

    def test_survives_malformed_records(self):
        for junk in ({}, {"protocolSection": None}, {"protocolSection": {"contactsLocationsModule": {"locations": [None, "x"]}}}):
            self.assertEqual(c.study_locations(junk), [])
            self.assertEqual(c.extract_row(junk)["title"], "")
            score, reasons = c.score_trial(junk, {"diagnosis": "Glioblastoma", "age": 55, "kps": 90})
            self.assertEqual(score, 0)


class Scoring(unittest.TestCase):
    INTAKE = {"diagnosis": "Glioblastoma", "age": 55, "kps": 90, "setting": "Recurrent", "keywords": ""}

    def test_every_adjustment_has_a_reason(self):
        score, reasons = c.score_trial(STUDIES["NCT06630260"], self.INTAKE)
        self.assertGreaterEqual(score, 30)
        self.assertTrue(all(re.search(r" \[(?:[+−]\d+|note)\]$", r) for r in reasons), reasons)
        self.assertFalse(any(r[0] in "=+-@" for r in reasons), "a reason must not start a spreadsheet formula")

    def test_age_limits(self):
        child = dict(self.INTAKE, age=4)
        score, reasons = c.score_trial(STUDIES["NCT06630260"], child)
        self.assertTrue(any(r.startswith("Minimum age") and r.endswith("[−30]") for r in reasons), reasons)
        _, adult_reasons = c.score_trial(STUDIES["NCT04094610"], self.INTAKE)   # paediatric / young adult trial
        self.assertTrue(any(r.startswith("Maximum age") for r in adult_reasons), adult_reasons)

    def test_missing_age_and_kps_do_not_penalise(self):
        score, reasons = c.score_trial(STUDIES["NCT06630260"], {"diagnosis": "Glioblastoma", "age": None, "kps": None})
        self.assertFalse(any("−" in r and ("age" in r or "KPS" in r or "ECOG" in r) for r in reasons), reasons)

    def test_performance_status_phrases(self):
        self.assertEqual(c.ecog_max("ECOG performance status 0-1"), 1)
        self.assertEqual(c.ecog_max("ECOG performance status of 0, 1 or 2"), 2)
        self.assertEqual(c.ecog_max("ECOG PS ≤ 2"), 2)
        self.assertIsNone(c.ecog_max("ECOG status documented"))
        self.assertEqual(c.kps_min("Karnofsky performance status ≥ 70"), 70)
        self.assertEqual(c.kps_min("KPS of at least 60%"), 60)
        self.assertEqual(c.kps_to_ecog(90), 1)

        def study(criteria):
            return {"protocolSection": {"identificationModule": {"briefTitle": "Glioblastoma study"},
                                        "eligibilityModule": {"eligibilityCriteria": criteria}}}

        _, r = c.score_trial(study("Karnofsky performance status ≥ 70"), dict(self.INTAKE, kps=60))
        self.assertIn("Needs KPS ≥ 70 [−15]", r)
        _, r = c.score_trial(study("Karnofsky performance status ≥ 70"), dict(self.INTAKE, kps=80))
        self.assertFalse(any("KPS" in x for x in r), r)
        _, r = c.score_trial(study("ECOG performance status 0-1"), dict(self.INTAKE, kps=70))
        self.assertTrue(any("ECOG 0–1" in x for x in r), r)

    def test_rank_hides_and_counts_unrelated(self):
        rows, hidden = c.rank(FIXTURE["studies"], self.INTAKE)
        self.assertEqual(hidden, 1)
        self.assertNotIn("NCT07018024", [r["nct"] for r in rows])
        self.assertEqual(rows, sorted(rows, key=lambda r: (-r["score"], r["title"].lower())))
        rows_all, hidden_all = c.rank(FIXTURE["studies"], self.INTAKE, include_off_topic=True)
        self.assertEqual((len(rows_all), hidden_all), (len(rows) + 1, 0))


class CtisRecruitment(unittest.TestCase):
    TODAY = "2026-11-10"

    def state(self, detail, code=4):
        return build_snapshot.recruitment_from_detail(detail, code, self.TODAY)

    def test_started_and_not_ended(self):
        d = ctis_detail({"Spain": [("START_OF_TRIAL", "2024-10-23"), ("START_OF_RECRUITMENT", "2024-11-21")],
                         "Poland": [("START_OF_TRIAL", "2026-06-15")]})
        self.assertEqual(self.state(d), ("recruiting", ["Spain"]))

    def test_end_date_without_a_notification_closes_recruitment(self):
        # Real shape of 2023-507575-22-00: no END_OF_RECRUITMENT event, but an
        # end date recorded on the recruitment period. Was reported as recruiting.
        d = ctis_detail({"Norway": [("START_OF_TRIAL", "2019-09-25"), ("START_OF_RECRUITMENT", "2019-10-21")]},
                        {"Norway": {"recruitmentStartDate": "2019-10-21", "recruitmentEndDate": "2025-03-31"}})
        self.assertEqual(self.state(d, 5), ("closed", []))

    def test_future_end_date_is_still_open(self):
        d = ctis_detail({"Italy": [("START_OF_RECRUITMENT", "2026-01-01")]},
                        {"Italy": {"recruitmentEndDate": "2027-06-30"}})
        self.assertEqual(self.state(d), ("recruiting", ["Italy"]))

    def test_latest_event_wins(self):
        halted = ctis_detail({"France": [("START_OF_RECRUITMENT", "2024-02-26"), ("RESTART_OF_RECRUITMENT", "2026-07-08"),
                                         ("END_OF_RECRUITMENT", "2026-10-19"), ("TEMPORARY_HALT", "2026-10-19")]})
        self.assertEqual(self.state(halted), ("closed", []))
        restarted = ctis_detail({"France": [("START_OF_RECRUITMENT", "2022-06-03"), ("TEMPORARY_HALT", "2024-09-02"),
                                            ("RESTART_OF_RECRUITMENT", "2026-02-04")]})
        self.assertEqual(self.state(restarted), ("recruiting", ["France"]))

    def test_same_day_start_and_end_counts_as_closed(self):
        d = ctis_detail({"Belgium": [("START_OF_RECRUITMENT", "2025-11-10"), ("END_OF_RECRUITMENT", "2025-11-10")]})
        self.assertEqual(self.state(d), ("closed", []))

    def test_disagreement_with_status_code_is_reported_not_resolved(self):
        d = ctis_detail({"France": [("START_OF_RECRUITMENT", "2022-05-27"), ("RESTART_OF_RECRUITMENT", "2025-08-19")]})
        self.assertEqual(self.state(d, 5), ("uncertain", ["France"]))

    def test_not_started_and_unknown(self):
        self.assertEqual(self.state(ctis_detail({"Latvia": [("START_OF_TRIAL", "2025-03-06")]}), 3), ("not_started", []))
        self.assertEqual(self.state({}), ("unknown", []))
        self.assertEqual(self.state({"events": {"trialEvents": []}}), ("unknown", []))

    def test_dates_and_ids(self):
        self.assertEqual(build_snapshot.iso_date("24/07/2026"), "2026-07-24")
        self.assertEqual(build_snapshot.iso_date("2026-01-16T02:32:25"), "2026-01-16")
        self.assertEqual(build_snapshot.iso_date(None), "")
        self.assertEqual(build_snapshot.parse_countries(["Norway:5", "Spain:4"]), ["Norway", "Spain"])
        detail = {"authorizedApplication": {"authorizedPartI": {"trialDetails": {"clinicalTrialIdentifiers": {
            "secondaryIdentifyingNumbers": {"nctNumber": {"number": "nct 05739942"},
                                            "additionalRegistries": [{"number": "IND 130465"}]}}}}}}
        self.assertEqual(build_snapshot.other_ids_from_detail(detail), ["NCT05739942"])

    def test_sites_carry_no_contact_people(self):
        detail = {"authorizedApplication": {"authorizedPartsII": [{
            "mscInfo": {"mscName": "Norway"},
            "trialSites": [{"organisationAddressInfo": {
                "organisation": {"name": "Oslo University Hospital HF"},
                "address": {"city": "Oslo", "countryName": "Norway"},
                "email": "someone@example.org", "phone": "+47"},
                "personInfo": {"firstName": "A", "lastName": "B", "email": "someone@example.org"}}]}]}}
        sites = build_snapshot.sites_from_detail(detail)
        self.assertEqual(sites, [{"facility": "Oslo University Hospital HF", "city": "Oslo", "country": "Norway"}])


class IctrpFields(unittest.TestCase):
    def test_recruiting_flag(self):
        yes = ["Recruiting", "Pending", "Open to Recruitment", "Not Yet Recruiting", "Pending (Not yet recruiting)"]
        no = ["Recruiting stopped after recruiting started", "Not Recruiting", "Completed", "Complete: follow-up continuing",
              "Active, not recruiting", "Withdrawn", "Without startig enrollment", "Unknown", "Other (Terminated)", ""]
        for s in yes:
            self.assertTrue(build_ictrp.is_recruiting(s), s)
        for s in no:
            self.assertFalse(build_ictrp.is_recruiting(s), s)

    def test_dates(self):
        cases = {"2025-06-13": "2025-06-13", "2025/06/13": "2025-06-13", "01-09-2026": "2026-09-01",
                 "13/06/2025": "2025-06-13", "14 September 2026": "2026-09-14", "9 Sep 2024": "2024-09-09",
                 "": "", "soon": "", "31/02/20": ""}
        for raw, want in cases.items():
            self.assertEqual(build_ictrp.iso_date(raw), want, raw)

    def test_enrolment(self):
        self.assertEqual(build_ictrp.parse_enrolment("100"), 100)
        self.assertEqual(build_ictrp.parse_enrolment("Group 1:5;Group 2:5;Group 3:5;"), 15)
        self.assertEqual(build_ictrp.parse_enrolment("Case series:180;"), 180)
        self.assertIsNone(build_ictrp.parse_enrolment(""))
        self.assertIsNone(build_ictrp.parse_enrolment("to be decided"))

    def test_cross_ids(self):
        self.assertEqual(build_ictrp.cross_ids("NCT05697874; U1111-1234-5678"), ["NCT05697874"])
        self.assertEqual(build_ictrp.cross_ids("ISRCTN 30985676, 2023-506464-14-00"), ["ISRCTN30985676", "2023-506464-14-00"])
        self.assertEqual(build_ictrp.cross_ids("NIL"), [])


class IctrpState(unittest.TestCase):
    """The resumable state must refresh old records and never cache a failure."""

    def run_detail(self, state, fetch, **kw):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text(json.dumps(state), encoding="utf-8")
            original, pause = build_ictrp.fetch_detail, build_ictrp.PAUSE
            build_ictrp.fetch_detail, build_ictrp.PAUSE = fetch, 0
            try:
                build_ictrp.build(path, "detail", [], 1000, **kw)
            finally:
                build_ictrp.fetch_detail, build_ictrp.PAUSE = original, pause
            return json.loads(path.read_text(encoding="utf-8"))

    def test_old_records_are_refetched_and_fresh_ones_left(self):
        now = datetime(2026, 11, 10, tzinfo=timezone.utc)
        old = (now - timedelta(days=30)).isoformat(timespec="seconds")
        fresh = (now - timedelta(days=1)).isoformat(timespec="seconds")
        state = {"ids": {"A": "", "B": "", "C": ""},
                 "detail": {"A": {"rec": {"id": "A", "status": "Recruiting"}, "fetched": old},
                            "B": {"rec": {"id": "B", "status": "Recruiting"}, "fetched": fresh}}}
        fetched = []

        def fetch(tid):
            fetched.append(tid)
            return {"id": tid, "status": "Completed"}

        after = self.run_detail(state, fetch, max_age_days=6, now=now)
        self.assertEqual(sorted(fetched), ["A", "C"])
        self.assertEqual(after["detail"]["A"]["rec"]["status"], "Completed")
        self.assertEqual(after["detail"]["B"]["rec"]["status"], "Recruiting")

    def test_a_failed_fetch_is_retried_next_time_and_keeps_the_old_copy(self):
        now = datetime(2026, 11, 10, tzinfo=timezone.utc)
        old = (now - timedelta(days=30)).isoformat(timespec="seconds")
        state = {"ids": {"A": "", "C": ""}, "detail": {"A": {"rec": {"id": "A"}, "fetched": old}}}

        def fetch(tid):
            raise RuntimeError("HTTP 403")

        after = self.run_detail(state, fetch, max_age_days=6, now=now)
        self.assertEqual(after["detail"]["A"]["fetched"], old)   # still due
        self.assertNotIn("C", after["detail"])                    # not cached as a failure

    def test_state_written_by_the_old_script_is_migrated(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text(json.dumps({"ids": {"A": "", "B": ""},
                                        "detail": {"A": {"id": "A", "title": "old layout"}, "B": None}}))
            state = build_ictrp.load_state(path)
            self.assertEqual(state["detail"], {})
            self.assertEqual(sorted(state["ids"]), ["A", "B"])

    def test_write_recomputes_recruiting_and_reports_counts(self):
        now = datetime(2026, 11, 10, tzinfo=timezone.utc)
        stamp = now.isoformat(timespec="seconds")
        rec = {"id": "X", "title": "t", "registry": "CTRI", "recruiting": True,
               "status": "Recruiting stopped after recruiting started"}
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text(json.dumps({"ids": {"X": ""}, "detail": {"X": {"rec": rec, "fetched": stamp}}}))
            snap = build_ictrp.build(path, "write", [], 0, now=now)
        self.assertEqual((snap["count"], snap["recruitingCount"]), (1, 0))
        self.assertEqual(snap["byRegistry"], {"CTRI (India)": 1})
        self.assertEqual(snap["schema"], build_ictrp.SCHEMA)


class Snapshots(unittest.TestCase):
    """The committed snapshots must have the shape the page reads."""

    def load(self, name):
        return json.loads((ROOT / "docs" / "data" / name).read_text(encoding="utf-8"))

    def test_ctis_snapshot_shape(self):
        snap = self.load("eu_ctis.json")
        self.assertEqual(snap["count"], len(snap["trials"]))
        self.assertGreater(snap["count"], 40)
        states = {"recruiting", "closed", "not_started", "uncertain", "unknown"}
        for t in snap["trials"]:
            self.assertTrue(t["id"] and t["title"] and t["url"].startswith("https://"), t.get("id"))
            self.assertIn(t["recruitment"], states)
            self.assertRegex(t["lastUpdated"], r"^(\d{4}-\d{2}-\d{2})?$")
            if t["recruitment"] == "recruiting":
                self.assertTrue(t["recruitingCountries"], t["id"])
        self.assertNotIn("@", json.dumps([t.get("sites") for t in snap["trials"]]), "site contacts must not be stored")

    def test_ictrp_snapshot_shape(self):
        snap = self.load("ictrp.json")
        self.assertEqual(snap["count"], len(snap["trials"]))
        self.assertGreater(snap["count"], 30)
        for t in snap["trials"]:
            self.assertTrue(t["id"] and t["title"] and t["url"].startswith("https://"), t.get("id"))
            self.assertFalse(t["id"].upper().startswith(("NCT", "ISRCTN", "EUCTR")), t["id"])
            self.assertEqual(t["recruiting"], build_ictrp.is_recruiting(t["status"]), t["id"])
            self.assertRegex(t["lastUpdated"], r"^(\d{4}-\d{2}-\d{2})?$")
            self.assertTrue(t["enrolled"] is None or 0 <= t["enrolled"] < 1_000_000, t["id"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
