from __future__ import annotations

import http.cookiejar
import re
import socket
import time
from dataclasses import dataclass, replace
from html.parser import HTMLParser
from typing import Iterable
from urllib.error import URLError
from urllib.parse import urlencode, urljoin
from urllib.request import HTTPCookieProcessor, Request, build_opener

from .models import TaxRecord


HARVEY_TAX_SEARCH_URL = (
    "https://ks1355.cichosting.com/ttp/Tax/Search/search_tax.aspx"
)
USER_AGENT = "BLU-Tax-Agent/7C (+property-research)"
CAMA_LENGTHS = (3, 2, 1, 2, 2, 3, 2, 1)


@dataclass(frozen=True)
class TaxYearStatus:
    year: int
    total_due: float
    first_half_paid: bool | None
    second_half_paid: bool | None
    line_count: int


class _PageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.forms: list[dict] = []
        self._form: dict | None = None
        self._select: dict | None = None
        self.links: list[dict] = []
        self._link: dict | None = None

        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell_parts: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        tag = tag.lower()

        if tag == "form":
            self._form = {
                "action": attrs.get("action", ""),
                "inputs": [],
                "selects": [],
            }
            self.forms.append(self._form)

        elif tag == "input" and self._form is not None:
            self._form["inputs"].append(attrs)

        elif tag == "select" and self._form is not None:
            self._select = {
                "name": attrs.get("name", ""),
                "options": [],
            }
            self._form["selects"].append(self._select)

        elif tag == "option" and self._select is not None:
            self._select["options"].append({
                "value": attrs.get("value", ""),
                "selected": "selected" in attrs,
            })

        elif tag == "a":
            self._link = {
                "href": attrs.get("href", ""),
                "text": "",
            }
            self.links.append(self._link)

        elif tag == "tr":
            self._row = []

        elif tag in ("td", "th") and self._row is not None:
            self._cell_parts = []

    def handle_endtag(self, tag):
        tag = tag.lower()

        if tag == "form":
            self._form = None

        elif tag == "select":
            self._select = None

        elif tag == "a":
            self._link = None

        elif tag in ("td", "th") and self._row is not None:
            value = " ".join(self._cell_parts or [])
            value = re.sub(r"\s+", " ", value).strip()
            self._row.append(value)
            self._cell_parts = None

        elif tag == "tr":
            if self._row:
                self.rows.append(self._row)
            self._row = None

    def handle_data(self, data):
        value = re.sub(r"\s+", " ", data).strip()
        if not value:
            return

        if self._link is not None:
            self._link["text"] = (
                self._link["text"] + " " + value
            ).strip()

        if self._cell_parts is not None:
            self._cell_parts.append(value)


def _parse_page(html: str) -> _PageParser:
    parser = _PageParser()
    parser.feed(html)
    return parser


def _money(value: str) -> float:
    raw = re.sub(r"[^0-9.\-]", "", value or "")
    if not raw or raw in {".", "-", "-."}:
        return 0.0
    return float(raw)


def _paid_flag(value: str) -> bool | None:
    text = (value or "").strip().upper()
    if text == "YES":
        return True
    if text == "NO":
        return False
    return None


def _header_key(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (value or "").upper())


def parse_tax_year_table(html: str) -> dict[int, TaxYearStatus]:
    """Parse CIC Current Taxes or Tax History table into one status per year."""
    rows = _parse_page(html).rows
    header_index = None
    header_map = {}

    for idx, row in enumerate(rows):
        keys = [_header_key(cell) for cell in row]
        if "YEAR" in keys and "TOTALDUE" in keys:
            header_index = idx
            header_map = {key: i for i, key in enumerate(keys)}
            break

    if header_index is None:
        return {}

    year_i = header_map["YEAR"]
    total_i = header_map["TOTALDUE"]
    first_paid_i = header_map.get("1STHALFPAID")
    second_paid_i = header_map.get("2NDHALFPAID")

    accum: dict[int, dict] = {}

    for row in rows[header_index + 1:]:
        if year_i >= len(row):
            continue
        year_text = row[year_i].strip()
        if not re.fullmatch(r"20\d{2}", year_text):
            continue

        year = int(year_text)
        total_due = _money(row[total_i]) if total_i < len(row) else 0.0
        first_paid = (
            _paid_flag(row[first_paid_i])
            if first_paid_i is not None and first_paid_i < len(row)
            else None
        )
        second_paid = (
            _paid_flag(row[second_paid_i])
            if second_paid_i is not None and second_paid_i < len(row)
            else None
        )

        bucket = accum.setdefault(
            year,
            {
                "total_due": 0.0,
                "first_flags": [],
                "second_flags": [],
                "line_count": 0,
            },
        )
        bucket["total_due"] += total_due
        bucket["first_flags"].append(first_paid)
        bucket["second_flags"].append(second_paid)
        bucket["line_count"] += 1

    result: dict[int, TaxYearStatus] = {}

    for year, bucket in accum.items():
        first_values = [v for v in bucket["first_flags"] if v is not None]
        second_values = [v for v in bucket["second_flags"] if v is not None]

        # A year is fully paid only when every line says Yes for that half.
        first_paid = (
            all(first_values)
            if first_values
            else None
        )
        second_paid = (
            all(second_values)
            if second_values
            else None
        )

        result[year] = TaxYearStatus(
            year=year,
            total_due=round(bucket["total_due"], 2),
            first_half_paid=first_paid,
            second_half_paid=second_paid,
            line_count=bucket["line_count"],
        )

    return result


def unpaid_tax_years(
    statuses: dict[int, TaxYearStatus],
) -> tuple[int, ...]:
    years = []
    for year, status in statuses.items():
        if status.total_due > 0.005:
            years.append(year)
            continue
        if status.first_half_paid is False or status.second_half_paid is False:
            years.append(year)
    return tuple(sorted(years))


def displayed_total_due(
    statuses: dict[int, TaxYearStatus],
    years: Iterable[int] | None = None,
) -> float:
    wanted = set(years) if years is not None else set(statuses)
    return round(
        sum(
            status.total_due
            for year, status in statuses.items()
            if year in wanted
        ),
        2,
    )


def consecutive_unpaid_years(
    statuses: dict[int, TaxYearStatus],
    *,
    current_year: int,
) -> tuple[int, ...]:
    unpaid = set(unpaid_tax_years(statuses))
    years = []
    year = current_year
    while year in unpaid:
        years.append(year)
        year -= 1
    return tuple(sorted(years))


class HarveyCurrentTaxClient:
    def __init__(self, *, timeout: int = 30):
        self.timeout = timeout
        self.jar = http.cookiejar.CookieJar()
        self.opener = build_opener(HTTPCookieProcessor(self.jar))

    def _request(
        self,
        url: str,
        *,
        data=None,
        referer: str | None = None,
        attempts: int = 3,
        retry_delay: float = 0.75,
    ):
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
            ),
        }
        if referer:
            headers["Referer"] = referer
        if data is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded"

        attempts = max(1, int(attempts))
        last_error = None

        for attempt in range(1, attempts + 1):
            request = Request(url, data=data, headers=headers)
            try:
                with self.opener.open(
                    request,
                    timeout=self.timeout,
                ) as response:
                    return (
                        response.geturl(),
                        response.read().decode("utf-8", errors="replace"),
                    )
            except (URLError, TimeoutError, socket.timeout) as exc:
                last_error = exc
                if attempt >= attempts:
                    raise
                print(
                    f"[Harvey CIC retry] attempt {attempt}/{attempts} "
                    f"failed: {type(exc).__name__}: {exc}"
                )
                if retry_delay > 0:
                    time.sleep(retry_delay * attempt)

        raise last_error

    @staticmethod
    def _cama_segments(pidno: str) -> list[str]:
        pidno = str(pidno or "").strip()
        if len(pidno) != 16 or not pidno.isdigit():
            raise ValueError(f"Expected 16-digit Harvey PIDNO, got {pidno!r}")

        values = []
        pos = 0
        for length in CAMA_LENGTHS:
            values.append(pidno[pos:pos + length])
            pos += length
        return values

    @staticmethod
    def _choose_form(parser: _PageParser) -> dict:
        if not parser.forms:
            raise RuntimeError("Harvey tax search page returned no form")
        return max(parser.forms, key=lambda form: len(form["inputs"]))

    @staticmethod
    def _cama_fields(form: dict) -> list[str]:
        candidates = []
        for inp in form["inputs"]:
            typ = (inp.get("type") or "text").lower()
            if typ != "text":
                continue
            try:
                maxlength = int(inp.get("maxlength", ""))
            except ValueError:
                continue
            candidates.append((inp.get("name", ""), maxlength))

        target = list(CAMA_LENGTHS)
        for start in range(0, len(candidates) - len(target) + 1):
            group = candidates[start:start + len(target)]
            if [length for _, length in group] == target:
                return [name for name, _ in group]
        return []

    @staticmethod
    def _base_payload(form: dict) -> dict[str, str]:
        payload = {}

        for inp in form["inputs"]:
            name = inp.get("name")
            if not name:
                continue
            typ = (inp.get("type") or "text").lower()

            if typ == "hidden":
                payload[name] = inp.get("value", "")
            elif typ == "radio" and "checked" in inp:
                payload[name] = inp.get("value", "")

        for select in form["selects"]:
            name = select.get("name")
            if not name:
                continue
            selected = next(
                (o for o in select["options"] if o.get("selected")),
                None,
            )
            if selected is not None:
                payload[name] = selected.get("value", "")

        return payload

    @staticmethod
    def _find_link(html: str, label: str) -> str:
        parser = _parse_page(html)
        for link in parser.links:
            if label.lower() in (link.get("text") or "").lower():
                return link.get("href") or ""
        return ""

    @staticmethod
    def _verify_result_identity(
        html: str,
        *,
        pidno: str,
        tax_id: str,
    ) -> bool:
        parser = _parse_page(html)

        # The result page exposes a direct official appraiser link containing
        # the 16-digit PIDNO.  Require that exact identity.
        expected = f"details.aspx?pid={pidno}".lower()
        if any(
            expected in (link.get("href") or "").lower()
            for link in parser.links
        ):
            return True

        # Fallback: require the exact TaxID/parcel in a CIC result URL.
        tax_id = str(tax_id or "").strip()
        if tax_id and any(
            f"_taxparcel={tax_id}".lower() in (link.get("href") or "").lower()
            for link in parser.links
        ):
            return True

        return False

    def search_due_only(self, *, pidno: str, tax_id: str = "") -> dict:
        search_url, search_html = self._request(HARVEY_TAX_SEARCH_URL)
        parser = _parse_page(search_html)
        form = self._choose_form(parser)
        fields = self._cama_fields(form)

        if len(fields) != 8:
            raise RuntimeError("Could not map Harvey 8-part CAMA search fields")

        payload = self._base_payload(form)
        for name, value in zip(fields, self._cama_segments(pidno)):
            payload[name] = value

        payload["chkRealEstate"] = "on"
        payload["OnlyTaxesDuesCheckBox"] = "on"
        payload["btnFindNow.x"] = "1"
        payload["btnFindNow.y"] = "1"

        action = urljoin(search_url, form["action"] or search_url)
        result_url, result_html = self._request(
            action,
            data=urlencode(payload).encode("utf-8"),
            referer=search_url,
        )

        current_href = self._find_link(result_html, "Current Taxes")
        history_href = self._find_link(result_html, "Tax History")

        if not current_href and not history_href:
            return {
                "found_due": False,
                "identity_verified": False,
                "result_url": result_url,
                "statuses": {},
            }

        identity_verified = self._verify_result_identity(
            result_html,
            pidno=pidno,
            tax_id=tax_id,
        )
        if not identity_verified:
            raise RuntimeError(
                f"Harvey tax result identity could not be verified for PIDNO {pidno}"
            )

        statuses: dict[int, TaxYearStatus] = {}

        if history_href:
            history_url = urljoin(result_url, history_href)
            _, history_html = self._request(
                history_url,
                referer=result_url,
            )
            statuses.update(parse_tax_year_table(history_html))

        if current_href:
            current_url = urljoin(result_url, current_href)
            _, current_html = self._request(
                current_url,
                referer=result_url,
            )
            # Current Taxes wins for a duplicate current year.
            statuses.update(parse_tax_year_table(current_html))

        return {
            "found_due": bool(unpaid_tax_years(statuses)),
            "identity_verified": True,
            "result_url": result_url,
            "statuses": statuses,
        }


def apply_current_tax_verification(
    record: TaxRecord,
    result: dict,
) -> TaxRecord | None:
    """Return a verified record, or None when no current taxes are due."""
    if not result.get("found_due"):
        return None

    statuses = result.get("statuses") or {}
    years = unpaid_tax_years(statuses)
    if not years:
        return None

    amount = displayed_total_due(statuses, years)
    consecutive = consecutive_unpaid_years(
        statuses,
        current_year=max(years),
    )

    notes = "; ".join(
        n for n in (
            record.notes,
            (
                "Harvey CIC current-tax verification: unpaid tax year(s) "
                + ",".join(map(str, years))
                + f"; displayed Total Due across unpaid lines=${amount:,.2f}; "
                + "CIC warns displayed totals do not include all interest, "
                + "penalties, and fees; Treasurer payoff required."
            ),
            (
                "Consecutive unpaid run through latest tax year: "
                + ",".join(map(str, consecutive))
            ),
        )
        if n
    )

    return replace(
        record,
        delinquent_years=years,
        amount_due=amount,
        source_url=result.get("result_url") or HARVEY_TAX_SEARCH_URL,
        source_type="current_tax_verified",
        notes=notes,
    )


def verify_harvey_records(
    records: Iterable[TaxRecord],
    *,
    client: HarveyCurrentTaxClient | None = None,
    sleep_seconds: float = 0.15,
    limit: int = 0,
) -> tuple[list[TaxRecord], dict[str, int]]:
    client = client or HarveyCurrentTaxClient()
    rows = list(records)
    if limit and limit > 0:
        rows = rows[:limit]

    verified: list[TaxRecord] = []
    no_due = 0
    errors = 0
    identity_verified = 0

    for index, record in enumerate(rows):
        try:
            result = client.search_due_only(
                pidno=record.ain,
                tax_id=record.tax_id,
            )
            if result.get("identity_verified"):
                identity_verified += 1

            updated = apply_current_tax_verification(record, result)
            if updated is None:
                no_due += 1
            else:
                verified.append(updated)

        except Exception as exc:
            errors += 1
            print(
                f"[Harvey current tax verification error] "
                f"{record.address}, {record.city}: "
                f"{type(exc).__name__}: {exc}"
            )

        processed = index + 1
        if processed % 25 == 0 or processed == len(rows):
            print(
                f"[Harvey current tax progress] {processed}/{len(rows)} checked | "
                f"currently_due={len(verified)} | no_due={no_due} | errors={errors}"
            )

        if sleep_seconds > 0 and index + 1 < len(rows):
            time.sleep(sleep_seconds)

    def consecutive_count(record: TaxRecord) -> int:
        if not record.delinquent_years:
            return 0
        years = set(record.delinquent_years)
        latest = max(years)
        count = 0
        year = latest
        while year in years:
            count += 1
            year -= 1
        return count

    audit = {
        "input_records": len(rows),
        "identity_verified": identity_verified,
        "currently_due": len(verified),
        "no_current_due": no_due,
        "errors": errors,
        "verified_2plus_years": sum(
            1 for r in verified if len(r.delinquent_years) >= 2
        ),
        "verified_3plus_years": sum(
            1 for r in verified if len(r.delinquent_years) >= 3
        ),
        "verified_2plus_consecutive_latest": sum(
            1 for r in verified if consecutive_count(r) >= 2
        ),
        "verified_3plus_consecutive_latest": sum(
            1 for r in verified if consecutive_count(r) >= 3
        ),
    }
    return verified, audit
