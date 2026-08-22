from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.parse import urlparse

from .parser import discover_tax_document_links, extract_text_from_bytes


@dataclass(frozen=True)
class CountySource:
    county: str
    landing_pages: tuple[str, ...]
    allowed_domains: frozenset[str]


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
    ),
    CountySource(
        "Butler",
        ("https://www.bucoks.com/501/Real-Estate-Taxes",),
        frozenset({"www.bucoks.com", "bucoks.com"}),
    ),
)


def fetch_bytes(url: str, timeout: int = 30) -> bytes:
    request = Request(url, headers={"User-Agent": "BLU-Tax-Agent/1.0 (+property-research)"})
    with urlopen(request, timeout=timeout) as response:
        return response.read()


def discover_county_documents(source: CountySource) -> list[str]:
    docs: list[str] = []
    for page in source.landing_pages:
        html = fetch_bytes(page).decode("utf-8", errors="replace")
        for url in discover_tax_document_links(html, page, set(source.allowed_domains)):
            if url not in docs:
                docs.append(url)
    return docs


def read_document_url(url: str) -> str:
    suffix = Path(urlparse(url).path).suffix.lower()
    data = fetch_bytes(url)
    return extract_text_from_bytes(data, suffix or ".txt")
