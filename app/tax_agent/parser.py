from __future__ import annotations

import io
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse

from .models import TaxRecord

MONEY_RE = re.compile(r"\$?\s*([0-9][0-9,]*(?:\.\d{1,2})?)")
ANNUAL_YEAR_PATTERNS = (
    re.compile(r"(?i)\b(20\d{2})\s+(?:[A-Z ]+\s+)?REAL\s+ESTATE\s+DELINQUENT\s+TAX"),
    re.compile(r"(?i)tax(?:es)?\s+unpaid\s+for\s+(?:the\s+)?year\s+(20\d{2})"),
)


def parse_money(value: str) -> float | None:
    if not value:
        return None
    match = MONEY_RE.search(value)
    return float(match.group(1).replace(",", "")) if match else None


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


def infer_annual_tax_year(text: str) -> int | None:
    sample = (text or "")[:12000]
    for pattern in ANNUAL_YEAR_PATTERNS:
        match = pattern.search(sample)
        if match:
            return int(match.group(1))
    return None


def _split_location(value: str) -> tuple[str, str, str, str]:
    raw = re.sub(r"\s+", " ", (value or "").strip()).strip(" ,")
    if not raw:
        return "", "", "KS", ""

    parts = [p.strip() for p in raw.split(",")]
    address = parts[0] if parts else ""
    city = parts[1] if len(parts) > 1 else ""
    tail = parts[2] if len(parts) > 2 else ""
    state, zip_code = "KS", ""

    # County exhibits sometimes contain no situs address and literally report
    # only "Sedgwick County, KS" (or ", Sedgwick County, KS"). Treat that as
    # missing address rather than a usable property location.
    if address.upper().endswith(" COUNTY") and city.upper() == "KS":
        return "", "", "KS", ""

    m = re.search(r"\b([A-Z]{2})\s*(\d{5}(?:-\d{4})?)?\b", tail.upper())
    if m:
        state, zip_code = m.group(1), m.group(2) or ""
    if address.upper() in {"", "KS"}:
        address = ""
    return address, city, state, zip_code


def parse_foreclosure_exhibit(text: str, *, county: str, source_url: str = "") -> list[TaxRecord]:
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
        # PDF extraction occasionally drops the first E from REDEEMED.
        if re.search(r"(?im)^\s*(?:REDEEMED|RDEEMED)\s*$", block):
            status = "REDEEMED"
        elif re.search(r"(?im)^\s*DROPPED\s*$", block):
            status = "DROPPED"

        # Capture location through the next named field so addresses split across
        # PDF text lines (for example "344" + "W 34TH ST S ...") are rejoined.
        loc_match = re.search(
            r"(?is)\bApproximate\s+Location\s*:\s*(.*?)"
            r"(?=\s*Delinquent\s+Years\s*:|\s*Redemption\s+Amount\s*:|"
            r"\s*Current\s+Owner|\Z)",
            block,
        )
        address, city, state, zip_code = _split_location(loc_match.group(1) if loc_match else "")
        years_match = re.search(r"(?im)^\s*Delinquent\s+Years\s*:\s*([^\n]+)", block)
        # Redemption Amount may appear mid-line after Delinquent Years in PDF
        # extraction. Capture the numeric amount directly instead of requiring
        # the field to begin a new line.
        amount_match = re.search(
            r"(?i)\bRedemption\s+Amount\s*:\s*\$?\s*([0-9][0-9,]*(?:\.\d{1,2})?)",
            block,
        )
        owner_match = re.search(
            r"(?i)\bCurrent\s+Owner(?:\(s\)|s)?\s*:\s*([^\n]+)", block
        )

        records.append(TaxRecord(
            county=county, parcel_id=parcel_no, tax_id=tax_id, address=address, city=city,
            state=state, zip_code=zip_code, owner=owner_match.group(1).strip() if owner_match else "",
            delinquent_years=parse_years(years_match.group(1) if years_match else ""),
            amount_due=parse_money(amount_match.group(1) if amount_match else ""), status=status,
            source_url=source_url, source_type="foreclosure_exhibit",
        ))
    return records


def _harvey_foreclosure_year_from_court_case(court_case: str) -> int | None:
    match = re.search(r"(?i)(\d{2})-CV-", court_case or "")
    if not match:
        return None
    return 2000 + int(match.group(1))


def parse_harvey_foreclosure_notice(text: str, *, source_url: str = "") -> list[TaxRecord]:
    clean = re.sub(r"\r", "\n", text or "")
    court_match = re.search(
        r"(?im)\bNo\.\s*([A-Z]{0,3}-?\d{2}-CV-\d+)",
        clean,
    )
    court_case = court_match.group(1).strip() if court_match else ""
    foreclosure_year = _harvey_foreclosure_year_from_court_case(court_case)

    starts = list(
        re.finditer(
            r"(?im)\bCAUSE\s+(\d+)\s*\(\s*Parcel\s*#\s*([^) ;]+)",
            clean,
        )
    )
    rows: list[TaxRecord] = []

    for idx, match in enumerate(starts):
        end = starts[idx + 1].start() if idx + 1 < len(starts) else len(clean)
        block = clean[match.start():end]
        cause, parcel = match.group(1), match.group(2).strip()

        owner_match = re.search(
            r"(?im)Owners?\s+of\s+Record\s*:\s*([^\n]+)",
            block,
        )
        tax_match = re.search(
            r"(?is)Taxes\s+for\s+the\s+year\s+(20\d{2})\s+and\s+prior\s+years"
            r".*?[-–]\s*\$\s*([0-9][0-9,]*(?:\.\d{1,2})?)",
            block,
        )
        if not tax_match:
            continue

        through_year = int(tax_match.group(1))
        years = (through_year - 2, through_year - 1, through_year)
        case_id = (
            f"{foreclosure_year}|CAUSE-{cause}"
            if foreclosure_year is not None
            else f"CAUSE-{cause}"
        )

        notes = (
            f"Inferred minimum 3-year delinquency ending {through_year} from filed "
            "foreclosure notice; exact tax years require parcel verification."
        )
        if court_case:
            notes = f"Court case {court_case}. " + notes

        rows.append(
            TaxRecord(
                county="Harvey",
                parcel_id=parcel,
                tax_id=parcel,
                case_id=case_id,
                owner=owner_match.group(1).strip() if owner_match else "",
                delinquent_years=years,
                amount_due=float(tax_match.group(2).replace(",", "")),
                source_url=source_url,
                source_type="foreclosure_notice",
                notes=notes,
            )
        )

    return rows


def parse_harvey_news_status(text: str, *, source_url: str = "") -> list[TaxRecord]:
    rows: list[TaxRecord] = []
    source = text or ""

    preferred = re.compile(
        r"(?is)causes?\s+([0-9,\sand]+?)\s+from\s+the\s+(20\d{2})\s+"
        r"tax\s+foreclosure.*?\b(?:have|has)\s+been\s+redeemed"
    )
    matched_spans: list[tuple[int, int]] = []

    for match in preferred.finditer(source):
        matched_spans.append(match.span())
        year = match.group(2)
        for cause in re.findall(r"\d+", match.group(1)):
            rows.append(
                TaxRecord(
                    county="Harvey",
                    case_id=f"{year}|CAUSE-{cause}",
                    status="REDEEMED",
                    source_url=source_url,
                    source_type="foreclosure_status",
                    notes=(
                        f"Harvey County news update marks Cause {cause} from the "
                        f"{year} tax foreclosure redeemed."
                    ),
                )
            )

    remaining = source
    for start, end in reversed(matched_spans):
        remaining = remaining[:start] + " " * (end - start) + remaining[end:]

    for match in re.finditer(
        r"(?is)causes?\s+([0-9,\sand]+).*?\b(?:have|has)\s+been\s+redeemed",
        remaining,
    ):
        for cause in re.findall(r"\d+", match.group(1)):
            rows.append(
                TaxRecord(
                    county="Harvey",
                    case_id=f"CAUSE-{cause}",
                    status="REDEEMED",
                    source_url=source_url,
                    source_type="foreclosure_status",
                    notes="Harvey County news update marks this foreclosure cause redeemed.",
                )
            )

    return rows


def parse_annual_rows(text: str, *, county: str, tax_year: int, source_url: str = "") -> list[TaxRecord]:
    """Parse common annual delinquent real-estate publication rows."""
    records: list[TaxRecord] = []
    street_suffix = r"(?:ST|AVE|RD|DR|LN|CT|BLVD|PL|HWY|CIR|TER|WAY)"
    line_re = re.compile(
        rf"^\s*(?P<owner>.+?)\s+(?P<address>\d{{1,6}}\s+.+?\b{street_suffix}\b(?:\s+\w+)?)\s+"
        r"(?P<city>[A-Z .'-]+),?\s+KS\s+(?P<zip>\d{5}(?:-\d{4})?)\s+\$?\s*"
        r"(?P<amount>[0-9,]+(?:\.\d{1,2})?)\s*$",
        re.IGNORECASE,
    )
    for raw_line in text.splitlines():
        line = re.sub(r"\s+", " ", raw_line.strip())
        match = line_re.match(line)
        if not match:
            continue
        records.append(TaxRecord(
            county=county, address=match.group("address").strip(), city=match.group("city").strip(),
            zip_code=match.group("zip"), owner=match.group("owner").strip(),
            delinquent_years=(tax_year,), amount_due=parse_money(match.group("amount")),
            source_url=source_url, source_type="annual_publication",
            notes="Annual publication; property-location match should be verified.",
        ))
    return records


def html_to_text(html: str) -> str:
    class TextParser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.parts: list[str] = []
        def handle_data(self, data: str) -> None:
            value = re.sub(r"\s+", " ", data).strip()
            if value:
                self.parts.append(value)
    p = TextParser()
    p.feed(html)
    return "\n".join(p.parts)


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
    decoded = data.decode("utf-8", errors="replace")
    if suffix in {".html", ".htm", ""} and "<" in decoded and ">" in decoded:
        return html_to_text(decoded)
    return decoded


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
            self._href, self._text = None, []


def discover_tax_document_links(
    html: str, base_url: str, allowed_domains: set[str], *,
    parent_is_foreclosure: bool = False,
) -> list[str]:
    parser = _LinkParser()
    parser.feed(html)
    found: list[str] = []
    for href, label in parser.links:
        url = urljoin(base_url, href)
        parsed = urlparse(url)
        if parsed.hostname not in allowed_domains:
            continue
        haystack = f"{url} {label}".lower()
        tax_match = any(token in haystack for token in ("delinquent", "foreclosure", "tax sale", "exhibit", "sheriff sale"))
        support_match = parent_is_foreclosure and any(
            token in haystack
            for token in (
                "story map",
                "property list",
                "list of properties",
                "notice of sale",
                "sheriff sale",
            )
        )
        if not (tax_match or support_match):
            continue
        if any(token in haystack for token in ("personal property", "personal-property", "personal_tax")):
            continue
        if url not in found:
            found.append(url)
    return found
