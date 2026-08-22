from __future__ import annotations

import io
import re
from dataclasses import replace
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse

from .models import TaxRecord


MONEY_RE = re.compile(r"\$?\s*([0-9][0-9,]*(?:\.\d{1,2})?)")


def parse_money(value: str) -> float | None:
    if not value:
        return None
    match = MONEY_RE.search(value)
    if not match:
        return None
    return float(match.group(1).replace(",", ""))


def parse_years(value: str) -> tuple[int, ...]:
    years: set[int] = set()
    for start, end, single in re.findall(r"(?:(\d{4})\s*[-–]\s*(\d{4}))|(\d{4})", value or ""):
        if single:
            years.add(int(single))
        elif start and end:
            a, b = int(start), int(end)
            if a <= b and b - a <= 20:
                years.update(range(a, b + 1))
    return tuple(sorted(years))


def _split_location(value: str) -> tuple[str, str, str, str]:
    raw = re.sub(r"\s+", " ", value.strip())
    parts = [p.strip() for p in raw.split(",")]
    address = parts[0] if parts else ""
    city = parts[1] if len(parts) > 1 else ""
    tail = parts[2] if len(parts) > 2 else ""
    state = "KS"
    zip_code = ""
    m = re.search(r"\b([A-Z]{2})\s*(\d{5}(?:-\d{4})?)?\b", tail.upper())
    if m:
        state = m.group(1)
        zip_code = m.group(2) or ""
    if address in {"", "KS"}:
        address = ""
    return address, city, state, zip_code


def parse_foreclosure_exhibit(text: str, *, county: str, source_url: str = "") -> list[TaxRecord]:
    """Parse structured Kansas tax-foreclosure exhibits.

    Handles blocks with Parcel No., Tax ID No., Approximate Location,
    Delinquent Years, Redemption Amount and Current Owner(s). Resolved blocks
    marked REDEEMED or DROPPED are retained with that status so downstream
    merging can remove stale candidates.
    """
    clean = text.replace("\r", "\n")
    starts = list(re.finditer(r"(?im)^\s*Parcel\s+No\.\s*[:#]?\s*([^\n]+)", clean))
    records: list[TaxRecord] = []
    for idx, match in enumerate(starts):
        block_end = starts[idx + 1].start() if idx + 1 < len(starts) else len(clean)
        block = clean[match.start():block_end]
        parcel_no = match.group(1).strip()
        tax_match = re.search(r"(?im)^\s*Tax\s+ID\s+No\.\s*[:#]?\s*([^\n]+)", block)
        tax_id = tax_match.group(1).strip() if tax_match else ""
        status = "ACTIVE"
        if re.search(r"(?im)^\s*REDEEMED\s*$", block):
            status = "REDEEMED"
        elif re.search(r"(?im)^\s*DROPPED\s*$", block):
            status = "DROPPED"

        loc_match = re.search(r"(?im)^\s*Approximate\s+Location\s*:\s*([^\n]*)", block)
        address, city, state, zip_code = _split_location(loc_match.group(1) if loc_match else "")
        years_match = re.search(r"(?im)^\s*Delinquent\s+Years\s*:\s*([^\n]+)", block)
        amount_match = re.search(r"(?im)^\s*Redemption\s+Amount\s*:\s*([^\n]+)", block)
        owner_match = re.search(r"(?im)^\s*Current\s+Owner\(s\)\s*:\s*([^\n]+)", block)

        records.append(
            TaxRecord(
                county=county,
                parcel_id=parcel_no,
                tax_id=tax_id,
                address=address,
                city=city,
                state=state,
                zip_code=zip_code,
                owner=owner_match.group(1).strip() if owner_match else "",
                delinquent_years=parse_years(years_match.group(1) if years_match else ""),
                amount_due=parse_money(amount_match.group(1) if amount_match else ""),
                status=status,
                source_url=source_url,
                source_type="foreclosure_exhibit",
            )
        )
    return records


def parse_annual_rows(text: str, *, county: str, tax_year: int, source_url: str = "") -> list[TaxRecord]:
    """Best-effort parser for annual delinquent real-estate publication rows.

    County publications vary. This intentionally only accepts rows that expose
    a Kansas street/city address and dollar amount; ambiguous mailing-only rows
    should be reviewed rather than silently treated as property locations.
    """
    records: list[TaxRecord] = []
    line_re = re.compile(
        r"^\s*(?P<owner>.+?)\s{2,}(?P<address>\d{1,6}\s+.+?\b(?:ST|AVE|RD|DR|LN|CT|BLVD|PL|HWY)\b.*?)\s+(?P<city>[A-Z .'-]+),?\s+KS\s+(?P<zip>\d{5}(?:-\d{4})?)\s+\$?\s*(?P<amount>[0-9,]+(?:\.\d{1,2})?)\s*$",
        re.IGNORECASE,
    )
    for line in text.splitlines():
        match = line_re.match(re.sub(r"\s+", " ", line.strip()))
        if not match:
            continue
        records.append(
            TaxRecord(
                county=county,
                address=match.group("address").strip(),
                city=match.group("city").strip(),
                zip_code=match.group("zip"),
                owner=match.group("owner").strip(),
                delinquent_years=(tax_year,),
                amount_due=parse_money(match.group("amount")),
                source_url=source_url,
                source_type="annual_publication",
                notes="Annual publication; property-location match should be verified.",
            )
        )
    return records


def extract_text_from_bytes(data: bytes, suffix: str) -> str:
    suffix = suffix.lower()
    if suffix == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        return "\n".join((page.extract_text() or "") for page in reader.pages)
    if suffix == ".docx":
        from docx import Document

        doc = Document(io.BytesIO(data))
        return "\n".join(p.text for p in doc.paragraphs)
    return data.decode("utf-8", errors="replace")


class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "a":
            self._href = dict(attrs).get("href")
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._href is not None:
            self.links.append((self._href, " ".join(self._text).strip()))
            self._href = None
            self._text = []


def discover_tax_document_links(html: str, base_url: str, allowed_domains: set[str]) -> list[str]:
    parser = _LinkParser()
    parser.feed(html)
    found: list[str] = []
    for href, label in parser.links:
        url = urljoin(base_url, href)
        parsed = urlparse(url)
        if parsed.hostname not in allowed_domains:
            continue
        haystack = f"{url} {label}".lower()
        if not any(token in haystack for token in ("delinquent", "foreclosure", "tax sale", "exhibit")):
            continue
        if any(token in haystack for token in ("personal property", "personal-property", "personal_tax")):
            continue
        if url not in found:
            found.append(url)
    return found
