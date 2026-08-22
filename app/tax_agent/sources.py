from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .parser import (
    discover_tax_document_links,
    extract_text_from_bytes,
    infer_annual_tax_year,
    parse_annual_rows,
    parse_foreclosure_exhibit,
    parse_harvey_foreclosure_notice,
    parse_harvey_news_status,
)
from .models import TaxRecord


@dataclass(frozen=True)
class CountySource:
    county: str
    landing_pages: tuple[str, ...]
    allowed_domains: frozenset[str]
    sitemap_urls: tuple[str, ...] = ()


COUNTY_SOURCES: tuple[CountySource, ...] = (
    CountySource(
        "Sedgwick",
        (
            "https://www.sedgwickcounty.org/treasurer/delinquent-tax-lists/",
            "https://www.sedgwickcounty.org/treasurer/tax-foreclosure-auctions/",
        ),
        frozenset({"www.sedgwickcounty.org", "sedgwickcounty.org", "ssc.sedgwickcounty.org"}),
    ),
    CountySource(
        "Harvey",
        ("https://www.harveycounty.gov/taxes",),
        frozenset({"www.harveycounty.gov", "harveycounty.gov"}),
        ("https://www.harveycounty.gov/sitemap.xml",),
    ),
    CountySource(
        "Butler",
        (
            "https://www.bucoks.gov/501/Real-Estate-Taxes",
            "https://www.bucoks.gov/502/Tax-Foreclosure-Sale-Information",
        ),
        frozenset({
            "www.bucoks.gov", "bucoks.gov", "www.bucoks.com", "bucoks.com",
            "experience.arcgis.com",
        }),
    ),
)


def fetch_bytes(url: str, timeout: int = 30) -> bytes:
    request = Request(url, headers={"User-Agent": "BLU-Tax-Agent/2.0 (+property-research)"})
    with urlopen(request, timeout=timeout) as response:
        return response.read()


def _sitemap_tax_pages(xml: str, allowed_domains: frozenset[str]) -> list[str]:
    pages: list[str] = []
    for raw in re.findall(r"(?is)<loc>\s*(.*?)\s*</loc>", xml or ""):
        url = raw.replace("&amp;", "&").strip()
        host = urlparse(url).hostname
        hay = url.lower()
        if host not in allowed_domains:
            continue
        if not (
            ("tax" in hay and "foreclos" in hay)
            or "delinquent-tax" in hay
            or "tax-sale" in hay
        ):
            continue
        if url not in pages:
            pages.append(url)
    return pages


def _is_stale_annual_url(url: str, current_year: int, max_age: int = 4) -> bool:
    hay = url.lower()
    if not any(token in hay for token in ("del", "delinquent", "publication", "advertisinglist")):
        return False
    years = [int(y) for y in re.findall(r"(?<!\d)(20\d{2})(?!\d)", hay)]
    return bool(years) and max(years) < current_year - max_age


def discover_county_documents(source: CountySource, *, current_year: int | None = None) -> list[str]:
    current_year = current_year or date.today().year
    pages = list(source.landing_pages)

    for sitemap_url in source.sitemap_urls:
        try:
            xml = fetch_bytes(sitemap_url).decode("utf-8", errors="replace")
        except Exception:
            continue
        for page in _sitemap_tax_pages(xml, source.allowed_domains):
            if page not in pages:
                pages.append(page)

    docs: list[str] = []
    for page in pages:
        try:
            html = fetch_bytes(page).decode("utf-8", errors="replace")
        except Exception:
            continue
        parent_is_foreclosure = "foreclos" in page.lower() or "tax-sale" in page.lower()
        for url in discover_tax_document_links(
            html, page, set(source.allowed_domains), parent_is_foreclosure=parent_is_foreclosure
        ):
            clean = url.split("#", 1)[0]
            if clean in source.landing_pages:
                continue
            if _is_stale_annual_url(clean, current_year):
                continue
            if clean not in docs:
                docs.append(clean)

        # Keep official tax-foreclosure news pages discovered by sitemap; their
        # text can contain redemption updates even when no attachment is linked.
        if page not in source.landing_pages and parent_is_foreclosure and page not in docs:
            docs.append(page)
    return docs


def read_document_url(url: str) -> str:
    suffix = Path(urlparse(url).path).suffix.lower()
    data = fetch_bytes(url)
    return extract_text_from_bytes(data, suffix or ".html")


def parse_live_document(county: str, url: str, text: str) -> list[TaxRecord]:
    lower = (text or "").lower()
    if county == "Sedgwick" and "parcel no." in lower and "delinquent years" in lower:
        return parse_foreclosure_exhibit(text, county=county, source_url=url)

    if county == "Harvey":
        rows: list[TaxRecord] = []
        if "sheriff" in lower and "notice of sale" in lower and "parcel #" in lower:
            rows.extend(parse_harvey_foreclosure_notice(text, source_url=url))
        if "redeemed" in lower and "cause" in lower:
            rows.extend(parse_harvey_news_status(text, source_url=url))
        if rows:
            return rows

    tax_year = infer_annual_tax_year(text)
    if tax_year is not None and "real estate" in lower and "delinquent" in lower:
        return parse_annual_rows(text, county=county, tax_year=tax_year, source_url=url)
    return []


def collect_live_records(counties: set[str] | None = None) -> tuple[list[TaxRecord], list[tuple[str, str, int, str]]]:
    records: list[TaxRecord] = []
    audit: list[tuple[str, str, int, str]] = []
    wanted = {c.lower() for c in counties} if counties else None
    for source in COUNTY_SOURCES:
        if wanted and source.county.lower() not in wanted:
            continue
        for url in discover_county_documents(source):
            try:
                text = read_document_url(url)
                rows = parse_live_document(source.county, url, text)
                records.extend(rows)
                audit.append((source.county, url, len(rows), "OK"))
            except Exception as exc:
                audit.append((source.county, url, 0, f"{type(exc).__name__}: {exc}"))
    return records, audit
