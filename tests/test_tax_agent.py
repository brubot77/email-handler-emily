from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from app.tax_agent.core import build_candidates, merge_records, score_record
from app.tax_agent.models import TaxRecord
from app.tax_agent.parser import discover_tax_document_links, parse_foreclosure_exhibit, parse_years
from app.tax_agent.tracker import write_tracker


EXHIBIT = """
EXHIBIT A
SG-2025-CV-001114
Parcel No. 126
Tax ID No. 122521
Legal Description: LOTS 14-16 WABASH ADD., Sedgwick County, KS
Approximate Location: 1018 N WABASH AVE, WICHITA, KS 67214
Delinquent Years: 2020-2023
Redemption Amount: $2,554.25
Current Owner(s): WOODS STANLEY & SHERRI
Parcel No. 127
Tax ID No. 122596
REDEEMED
Parcel No. 130
Tax ID No. 123730
Approximate Location: 921 S TOPEKA AVE 1, WICHITA, KS 67211
Delinquent Years: 2020-2023
Redemption Amount: $4,480.52
Current Owner(s): EDS ENTERPRISES INC
Parcel No. 145
Tax ID No. 125040
DROPPED
"""


class ParserTests(unittest.TestCase):
    def test_parse_year_range_and_single(self):
        self.assertEqual(parse_years("2017-2020, 2023"), (2017, 2018, 2019, 2020, 2023))

    def test_foreclosure_exhibit_parses_and_marks_resolved(self):
        rows = parse_foreclosure_exhibit(EXHIBIT, county="Sedgwick", source_url="https://example.test/exhibit.pdf")
        self.assertEqual(len(rows), 4)
        first = rows[0]
        self.assertEqual(first.parcel_id, "126")
        self.assertEqual(first.tax_id, "122521")
        self.assertEqual(first.address, "1018 N WABASH AVE")
        self.assertEqual(first.city, "WICHITA")
        self.assertEqual(first.delinquent_years, (2020, 2021, 2022, 2023))
        self.assertEqual(first.amount_due, 2554.25)
        self.assertEqual(rows[1].status, "REDEEMED")
        self.assertEqual(rows[3].status, "DROPPED")

    def test_document_discovery_stays_on_official_domain(self):
        html = '''
        <a href="/media/current/exhibit-a.pdf">Tax Foreclosure Exhibit A</a>
        <a href="https://evil.example/delinquent.pdf">Delinquent real estate</a>
        <a href="/personal-property-delinquent.pdf">Personal Property Delinquent</a>
        '''
        links = discover_tax_document_links(html, "https://county.gov/taxes", {"county.gov"})
        self.assertEqual(links, ["https://county.gov/media/current/exhibit-a.pdf"])


class CoreTests(unittest.TestCase):
    def test_merge_accumulates_years(self):
        one = TaxRecord(county="Harvey", address="101 Main St", city="Newton", delinquent_years=(2023,), source_type="annual_publication")
        two = TaxRecord(county="Harvey", address="101 MAIN STREET", city="Newton", delinquent_years=(2024,), source_type="annual_publication")
        rows = merge_records([one, two])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].delinquent_years, (2023, 2024))

    def test_resolved_records_are_excluded(self):
        rows = parse_foreclosure_exhibit(EXHIBIT, county="Sedgwick")
        candidates = build_candidates(rows)
        self.assertEqual({c.record.parcel_id for c in candidates}, {"126", "130"})

    def test_value_limit_and_unknown_handling(self):
        low = TaxRecord(county="Butler", address="1 A ST", city="Andover", delinquent_years=(2023, 2024), appraised_value=110000)
        high = TaxRecord(county="Butler", address="2 A ST", city="Andover", delinquent_years=(2023, 2024), appraised_value=150000)
        unknown = TaxRecord(county="Butler", address="3 A ST", city="Andover", delinquent_years=(2023, 2024))
        c = build_candidates([low, high, unknown], max_value=130000, include_unknown_value=True)
        self.assertEqual({x.record.address for x in c}, {"1 A ST", "3 A ST"})
        unknown_c = next(x for x in c if x.record.address == "3 A ST")
        self.assertTrue(unknown_c.needs_manual_review)
        verified = build_candidates([low, high, unknown], max_value=130000, include_unknown_value=False)
        self.assertEqual([x.record.address for x in verified], ["1 A ST"])

    def test_foreclosure_record_scores_above_two_year_watch(self):
        fore = TaxRecord(county="Sedgwick", parcel_id="1", address="1 A ST", delinquent_years=(2020, 2021, 2022, 2023), source_type="foreclosure_exhibit", appraised_value=90000)
        watch = TaxRecord(county="Sedgwick", parcel_id="2", address="2 A ST", delinquent_years=(2023, 2024), source_type="annual_publication", appraised_value=90000)
        self.assertGreater(score_record(fore), score_record(watch))


class TrackerTests(unittest.TestCase):
    def test_manual_columns_survive_refresh(self):
        record = TaxRecord(county="Sedgwick", parcel_id="126", address="1018 N WABASH AVE", city="WICHITA", delinquent_years=(2020, 2021, 2022, 2023), source_type="foreclosure_exhibit", appraised_value=80000)
        candidate = build_candidates([record])[0]
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "tracker.csv"
            write_tracker(path, [candidate])
            with path.open(newline="", encoding="utf-8-sig") as f:
                rows = list(csv.DictReader(f))
            rows[0]["Review Status"] = "RESEARCH"
            rows[0]["Assigned To"] = "Billy"
            rows[0]["Notes"] = "Drive-by needed"
            with path.open("w", newline="", encoding="utf-8-sig") as f:
                writer = csv.DictWriter(f, fieldnames=rows[0].keys())
                writer.writeheader(); writer.writerows(rows)
            write_tracker(path, [candidate])
            with path.open(newline="", encoding="utf-8-sig") as f:
                refreshed = list(csv.DictReader(f))[0]
            self.assertEqual(refreshed["Review Status"], "RESEARCH")
            self.assertEqual(refreshed["Assigned To"], "Billy")
            self.assertEqual(refreshed["Notes"], "Drive-by needed")


if __name__ == "__main__":
    unittest.main()
