from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.tax_agent.core import build_candidates, merge_records, score_record
from app.tax_agent.models import TaxRecord
from app.tax_agent.parser import (
    discover_tax_document_links,
    infer_annual_tax_year,
    parse_annual_rows,
    parse_foreclosure_exhibit,
    parse_harvey_foreclosure_notice,
    parse_harvey_news_status,
    parse_years,
)
from app.tax_agent.sources import (
    CountySource,
    _is_stale_annual_url,
    _sitemap_tax_pages,
    discover_county_documents,
)
from app.tax_agent.tracker import write_tracker

EXHIBIT = """
EXHIBIT A
SG-2025-CV-001114
Parcel No. 126
Tax ID No. 122521
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

HARVEY_NOTICE = """
SHERIFF'S NOTICE OF SALE
CAUSE 9 (Parcel # 08943)
Owner of Record: Ricky Dale Eason
Lots One and Two, Newton, Harvey County, Kansas.
Taxes for the year 2024 and prior years with interest to January 13, 2025 - $3,067.53
CAUSE 11 (Parcel # 00124)
Owners of Record: Jacquelyn C. Havens
Lots Four, Five and Six, Walton, Harvey County, Kansas.
Taxes for the year 2024 and prior years with interest to January 13, 2025 - $11,145.48
"""

class ParserTests(unittest.TestCase):
    def test_parse_year_range_and_single(self):
        self.assertEqual(parse_years("2017-2020, 2023"), (2017, 2018, 2019, 2020, 2023))

    def test_foreclosure_exhibit_parses_and_marks_resolved(self):
        rows = parse_foreclosure_exhibit(EXHIBIT, county="Sedgwick")
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[0].parcel_id, "126")
        self.assertEqual(rows[0].delinquent_years, (2020, 2021, 2022, 2023))
        self.assertEqual(rows[1].status, "REDEEMED")
        self.assertEqual(rows[3].status, "DROPPED")

    def test_document_discovery_stays_on_official_domain(self):
        html = """
        <a href="/media/current/exhibit-a.pdf">Tax Foreclosure Exhibit A</a>
        <a href="https://evil.example/delinquent.pdf">Delinquent real estate</a>
        <a href="/personal-property-delinquent.pdf">Personal Property Delinquent</a>
        """
        links = discover_tax_document_links(html, "https://county.gov/taxes", {"county.gov"})
        self.assertEqual(links, ["https://county.gov/media/current/exhibit-a.pdf"])

    def test_annual_parser_handles_single_space_pdf_text(self):
        text = "ACUNA, DOUGLAS & LAURIE 414 N GREENVALLEY DR ANDOVER, KS 67002 $1507.10"
        rows = parse_annual_rows(text, county="Butler", tax_year=2025)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].address, "414 N GREENVALLEY DR")
        self.assertEqual(rows[0].city, "ANDOVER")
        self.assertEqual(rows[0].amount_due, 1507.10)

    def test_infer_annual_year(self):
        self.assertEqual(infer_annual_tax_year("2019 BUTLER COUNTY REAL ESTATE DELINQUENT TAX LIST"), 2019)

    def test_harvey_notice_and_redeemed_status_merge(self):
        rows = parse_harvey_foreclosure_notice(HARVEY_NOTICE)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].parcel_id, "08943")
        self.assertEqual(rows[0].tax_id, "CAUSE-9")
        self.assertEqual(rows[0].amount_due, 3067.53)
        self.assertEqual(rows[0].delinquent_years, (2022, 2023, 2024))
        status = parse_harvey_news_status("Since publication, causes 11 have been redeemed.")
        merged = merge_records(rows + status)
        cause11 = next(r for r in merged if r.tax_id == "CAUSE-11")
        self.assertTrue(cause11.is_resolved)

class SourceTests(unittest.TestCase):
    def test_butler_2019_publication_is_stale_in_2026(self):
        self.assertTrue(_is_stale_annual_url(
            "https://www.bucoks.gov/DocumentCenter/View/8038/2019-RE-Del-Publication", 2026
        ))
        self.assertFalse(_is_stale_annual_url(
            "https://www.bucoks.gov/DocumentCenter/View/9999/2025-RE-Del-Publication", 2026
        ))

    def test_harvey_sitemap_finds_tax_foreclosure_article(self):
        xml = """<urlset>
        <url><loc>https://www.harveycounty.gov/tax-foreclosure-sale-to-be-held-january-22</loc></url>
        <url><loc>https://www.harveycounty.gov/news</loc></url>
        </urlset>"""
        pages = _sitemap_tax_pages(xml, frozenset({"www.harveycounty.gov"}))
        self.assertEqual(pages, ["https://www.harveycounty.gov/tax-foreclosure-sale-to-be-held-january-22"])

    def test_harvey_discovery_crawls_sitemap_article_for_attachment(self):
        source = CountySource(
            "Harvey",
            ("https://www.harveycounty.gov/taxes",),
            frozenset({"www.harveycounty.gov"}),
            ("https://www.harveycounty.gov/sitemap.xml",),
        )
        payloads = {
            "https://www.harveycounty.gov/sitemap.xml": b"""<urlset><url><loc>https://www.harveycounty.gov/tax-foreclosure-sale-to-be-held-january-22</loc></url></urlset>""",
            "https://www.harveycounty.gov/taxes": b"<html></html>",
            "https://www.harveycounty.gov/tax-foreclosure-sale-to-be-held-january-22":
                b'<a href="/media/tax-foreclosure-sale-2026-notice-of-sheriff-sale.pdf">Notice of sheriff sale</a>',
        }
        with patch("app.tax_agent.sources.fetch_bytes", side_effect=lambda url, timeout=30: payloads[url]):
            docs = discover_county_documents(source, current_year=2026)
        self.assertIn("https://www.harveycounty.gov/media/tax-foreclosure-sale-2026-notice-of-sheriff-sale.pdf", docs)
        self.assertIn("https://www.harveycounty.gov/tax-foreclosure-sale-to-be-held-january-22", docs)

class CoreTests(unittest.TestCase):
    def test_merge_accumulates_years(self):
        one=TaxRecord(county="Harvey",address="101 Main St",city="Newton",delinquent_years=(2023,))
        two=TaxRecord(county="Harvey",address="101 MAIN STREET",city="Newton",delinquent_years=(2024,))
        rows=merge_records([one,two]); self.assertEqual(rows[0].delinquent_years,(2023,2024))

    def test_resolved_records_are_excluded(self):
        rows=parse_foreclosure_exhibit(EXHIBIT,county="Sedgwick")
        candidates=build_candidates(rows)
        self.assertEqual({c.record.parcel_id for c in candidates},{"126","130"})

    def test_value_limit_and_unknown_handling(self):
        low=TaxRecord(county="Butler",address="1 A ST",city="Andover",delinquent_years=(2023,2024),appraised_value=110000)
        high=TaxRecord(county="Butler",address="2 A ST",city="Andover",delinquent_years=(2023,2024),appraised_value=150000)
        unknown=TaxRecord(county="Butler",address="3 A ST",city="Andover",delinquent_years=(2023,2024))
        c=build_candidates([low,high,unknown])
        self.assertEqual({x.record.address for x in c},{"1 A ST","3 A ST"})

    def test_foreclosure_record_scores_above_two_year_watch(self):
        fore=TaxRecord(county="Sedgwick",parcel_id="1",address="1 A ST",delinquent_years=(2020,2021,2022,2023),source_type="foreclosure_exhibit",appraised_value=90000)
        watch=TaxRecord(county="Sedgwick",parcel_id="2",address="2 A ST",delinquent_years=(2023,2024),source_type="annual_publication",appraised_value=90000)
        self.assertGreater(score_record(fore),score_record(watch))

class TrackerTests(unittest.TestCase):
    def test_manual_columns_survive_refresh(self):
        record=TaxRecord(county="Sedgwick",parcel_id="126",address="1018 N WABASH AVE",city="WICHITA",delinquent_years=(2020,2021,2022,2023),source_type="foreclosure_exhibit",appraised_value=80000)
        candidate=build_candidates([record])[0]
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/"tracker.csv"; write_tracker(path,[candidate])
            with path.open(newline="",encoding="utf-8-sig") as f: rows=list(csv.DictReader(f))
            rows[0]["Review Status"]="RESEARCH";rows[0]["Assigned To"]="Billy";rows[0]["Notes"]="Drive-by needed"
            with path.open("w",newline="",encoding="utf-8-sig") as f:
                w=csv.DictWriter(f,fieldnames=rows[0].keys());w.writeheader();w.writerows(rows)
            write_tracker(path,[candidate])
            with path.open(newline="",encoding="utf-8-sig") as f: refreshed=list(csv.DictReader(f))[0]
            self.assertEqual(refreshed["Review Status"],"RESEARCH")
            self.assertEqual(refreshed["Assigned To"],"Billy")
            self.assertEqual(refreshed["Notes"],"Drive-by needed")

if __name__=="__main__": unittest.main()
