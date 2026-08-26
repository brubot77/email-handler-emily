from __future__ import annotations

import http.cookiejar
import re
import socket
import time
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from typing import Iterable
from urllib.error import URLError
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from urllib.request import HTTPCookieProcessor, Request, build_opener

from .harvey_current_tax import (
    TaxYearStatus,
    displayed_total_due,
    parse_tax_year_table,
    unpaid_tax_years,
)
from .models import TaxRecord


BUTLER_TAX_SEARCH_URL = (
    "https://portals.bucoks.com/taxportal/tax/Search/search_tax.aspx"
)
BUTLER_APPRAISER_URL = (
    "https://portals.bucoks.com/AppraiserPortal/appraiser/details.aspx?pid={pidno}"
)
BUTLER_GIS_LAYER = (
    "https://ww1.bucoks.com/bucogis1/rest/services/"
    "PublicOutreach/Parcels_Data_BldgLL_Condos/FeatureServer/0"
)
BUTLER_GIS_QUERY = BUTLER_GIS_LAYER + "/query"

USER_AGENT = "BLU-Tax-Agent/9A (+property-research)"
COUNTYWIDE_TRIGGER_MAP = "205"


@dataclass
class ButlerStatement:
    pidno: str
    raw_cama: str = ""
    tax_id: str = ""
    address: str = ""
    zip_code: str = ""
    owner: str = ""
    current_tax_url: str = ""
    tax_history_url: str = ""
    result_url: str = ""


@dataclass(frozen=True)
class ButlerAppraisal:
    tax_year: int
    property_class: str
    land_value: float
    improvement_value: float
    total_value: float


class _PageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.forms: list[dict] = []
        self._form: dict | None = None
        self._select: dict | None = None
        self.links: list[dict] = []
        self._link: dict | None = None
        self.spans: dict[str, str] = {}
        self._span_id: str | None = None
        self._span_parts: list[str] | None = None

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

        elif tag == "span":
            span_id = attrs.get("id", "")
            if span_id:
                self._span_id = span_id
                self._span_parts = []

    def handle_endtag(self, tag):
        tag = tag.lower()

        if tag == "form":
            self._form = None
        elif tag == "select":
            self._select = None
        elif tag == "a":
            self._link = None
        elif tag == "span" and self._span_id is not None:
            text = " ".join(self._span_parts or [])
            text = re.sub(r"\s+", " ", unescape(text)).strip()
            self.spans[self._span_id] = text
            self._span_id = None
            self._span_parts = None

    def handle_data(self, data):
        value = re.sub(r"\s+", " ", unescape(data)).strip()
        if not value:
            return
        if self._link is not None:
            self._link["text"] = (
                self._link["text"] + " " + value
            ).strip()
        if self._span_parts is not None:
            self._span_parts.append(value)


def _parse_page(html: str) -> _PageParser:
    parser = _PageParser()
    parser.feed(html)
    return parser


def _money(value: str) -> float:
    raw = re.sub(r"[^0-9.\-]", "", value or "")
    if not raw or raw in {".", "-", "-."}:
        return 0.0
    return float(raw)


def pid_from_cama(raw_cama: str) -> str:
    digits = re.sub(r"\D", "", str(raw_cama or ""))
    if len(digits) < 16:
        return ""
    return digits[:16]


def _qget(query: dict[str, list[str]], key: str) -> str:
    wanted = key.lower()
    for actual, values in query.items():
        if actual.lower() == wanted:
            return values[0] if values else ""
    return ""


def parse_appraiser_values(html: str) -> list[ButlerAppraisal]:
    parser = _parse_page(html)
    rows: dict[int, dict[str, str]] = {}

    patterns = {
        "tax_year": re.compile(r"ValuesTaxYear_(\d+)$", re.I),
        "property_class": re.compile(r"ValuesClass_(\d+)$", re.I),
        "land_value": re.compile(r"ValuesFinalLand_(\d+)$", re.I),
        "improvement_value": re.compile(r"ValuesFinalBldg_(\d+)$", re.I),
        "total_value": re.compile(r"ValuesFinalTotal_(\d+)$", re.I),
    }

    for span_id, text in parser.spans.items():
        for key, pattern in patterns.items():
            match = pattern.search(span_id)
            if match:
                rows.setdefault(int(match.group(1)), {})[key] = text
                break

    result: list[ButlerAppraisal] = []
    for index in sorted(rows):
        row = rows[index]
        year_text = row.get("tax_year", "")
        if not re.fullmatch(r"20\d{2}", year_text):
            continue
        result.append(
            ButlerAppraisal(
                tax_year=int(year_text),
                property_class=row.get("property_class", ""),
                land_value=_money(row.get("land_value", "")),
                improvement_value=_money(row.get("improvement_value", "")),
                total_value=_money(row.get("total_value", "")),
            )
        )
    return result


def latest_appraisal(html: str) -> ButlerAppraisal | None:
    rows = parse_appraiser_values(html)
    return max(rows, key=lambda row: row.tax_year) if rows else None


class ButlerClient:
    def __init__(self, *, timeout: int = 40):
        self.timeout = timeout
        self.jar = http.cookiejar.CookieJar()
        self.opener = build_opener(HTTPCookieProcessor(self.jar))

    def _request(
        self,
        url: str,
        *,
        data: bytes | None = None,
        referer: str | None = None,
        attempts: int = 3,
        retry_delay: float = 0.75,
    ) -> tuple[str, str]:
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
                with self.opener.open(request, timeout=self.timeout) as response:
                    return (
                        response.geturl(),
                        response.read().decode("utf-8", errors="replace"),
                    )
            except (URLError, TimeoutError, socket.timeout) as exc:
                last_error = exc
                if attempt >= attempts:
                    raise
                print(
                    f"[Butler retry] attempt {attempt}/{attempts} "
                    f"failed: {type(exc).__name__}: {exc}"
                )
                if retry_delay > 0:
                    time.sleep(retry_delay * attempt)

        raise last_error

    @staticmethod
    def _choose_form(parser: _PageParser) -> dict:
        if not parser.forms:
            raise RuntimeError("Butler tax search page returned no form")
        return max(parser.forms, key=lambda form: len(form["inputs"]))

    @staticmethod
    def _base_payload(form: dict) -> dict[str, str]:
        payload: dict[str, str] = {}
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
    def _blank_search_fields(payload: dict[str, str]) -> None:
        for name in (
            "txtMAP", "txtSEC", "txtSHT", "txtQTR", "txtBLK",
            "txtPRE", "txtSUF", "txtOWN",
            "txtOwnerTPayerId", "txtTaxInformation1", "txtTaxInformation2",
            "txtName", "txtStreetNumber", "txtStreetName",
            "txtBlock", "txtLot", "txtSection", "txtTownship",
            "txtRange", "txtLeaseName",
        ):
            payload[name] = ""

    @staticmethod
    def _timeout_page(html: str) -> bool:
        low = html.lower()
        return (
            "execution timeout expired" in low
            or ".net sqlclient data provider" in low
        )

    def start_countywide_due_search(self) -> tuple[str, str]:
        search_url, search_html = self._request(BUTLER_TAX_SEARCH_URL)
        parser = _parse_page(search_html)
        form = self._choose_form(parser)
        payload = self._base_payload(form)
        self._blank_search_fields(payload)

        payload.update({
            "chkRealEstate": "on",
            "OnlyTaxesDuesCheckBox": "on",
            "rbtType": "rbtBoth",
            "txtMAP": COUNTYWIDE_TRIGGER_MAP,
            "btnFindNow.x": "1",
            "btnFindNow.y": "1",
        })

        action = urljoin(search_url, form["action"] or search_url)
        result_url, result_html = self._request(
            action,
            data=urlencode(payload).encode("utf-8"),
            referer=search_url,
        )

        if self._timeout_page(result_html):
            raise RuntimeError(
                "Butler countywide discovery search hit the CIC SQL timeout"
            )

        statements = self.parse_result_statements(result_html, result_url)
        if not statements:
            raise RuntimeError(
                "Butler countywide discovery returned no identity-bearing tax links"
            )

        if all(
            statement.pidno.startswith(COUNTYWIDE_TRIGGER_MAP)
            for statement in statements.values()
        ):
            raise RuntimeError(
                "Butler CIC now appears to honor partial MAP criteria; "
                "countywide discovery strategy must be revalidated"
            )

        return result_url, result_html

    @staticmethod
    def parse_result_statements(
        html: str,
        result_url: str,
    ) -> dict[str, ButlerStatement]:
        parser = _parse_page(html)
        grouped: dict[str, ButlerStatement] = {}

        for link in parser.links:
            href = link.get("href") or ""
            absolute = urljoin(result_url, unescape(href).replace("&amp;", "&"))
            low_path = urlparse(absolute).path.lower()

            if not (
                low_path.endswith("/current_tax.aspx")
                or low_path.endswith("/tax_history.aspx")
            ):
                continue

            query = parse_qs(urlparse(absolute).query)
            raw_cama = _qget(query, "_CamaNumber")
            pidno = pid_from_cama(raw_cama)
            if not pidno:
                continue

            tax_unit = _qget(query, "_TaxUnit")
            tax_parcel = _qget(query, "_TaxParcel")
            tax_id = "-".join(
                value for value in (tax_unit, tax_parcel) if value
            )

            address = " ".join(
                value for value in (
                    _qget(query, "_StreetNumber"),
                    _qget(query, "_StreetDirection"),
                    _qget(query, "_StreetName"),
                )
                if value
            )

            statement = grouped.get(pidno)
            if statement is None:
                statement = ButlerStatement(
                    pidno=pidno,
                    raw_cama=raw_cama,
                    tax_id=tax_id,
                    address=address,
                    zip_code=_qget(query, "_ZipCode"),
                    owner=_qget(query, "_OwnerName1"),
                    result_url=result_url,
                )
                grouped[pidno] = statement
            else:
                statement.tax_id = statement.tax_id or tax_id
                statement.address = statement.address or address
                statement.zip_code = statement.zip_code or _qget(query, "_ZipCode")
                statement.owner = statement.owner or _qget(query, "_OwnerName1")
                statement.result_url = result_url

            if low_path.endswith("/current_tax.aspx"):
                statement.current_tax_url = absolute
            else:
                statement.tax_history_url = absolute

        return grouped

    @staticmethod
    def pagination_links(html: str, result_url: str) -> dict[int, str]:
        parser = _parse_page(html)
        pages: dict[int, str] = {}

        for link in parser.links:
            href = link.get("href") or ""
            absolute = urljoin(result_url, unescape(href).replace("&amp;", "&"))
            parsed = urlparse(absolute)
            if not parsed.path.lower().endswith("/search_tax_results.aspx"):
                continue

            query = parse_qs(parsed.query)
            page_text = _qget(query, "Page")
            if not page_text.isdigit():
                continue
            pages[int(page_text)] = absolute

        return pages

    def discover_due_statements(
        self,
        *,
        max_pages: int = 0,
        sleep_seconds: float = 0.05,
    ) -> tuple[list[ButlerStatement], dict[str, int]]:
        page1_url, page1_html = self.start_countywide_due_search()

        statements: dict[str, ButlerStatement] = {}
        pending_pages = self.pagination_links(page1_html, page1_url)
        visited = {1}

        def merge_page(page_html: str, page_url: str) -> None:
            for pidno, new in self.parse_result_statements(
                page_html,
                page_url,
            ).items():
                old = statements.get(pidno)
                if old is None:
                    statements[pidno] = new
                    continue

                old.tax_id = old.tax_id or new.tax_id
                old.address = old.address or new.address
                old.zip_code = old.zip_code or new.zip_code
                old.owner = old.owner or new.owner
                old.current_tax_url = old.current_tax_url or new.current_tax_url
                old.tax_history_url = old.tax_history_url or new.tax_history_url
                if new.current_tax_url or new.tax_history_url:
                    old.result_url = new.result_url

        merge_page(page1_html, page1_url)
        print(
            f"[Butler discovery] page=1 "
            f"unique_statements={len(statements)}"
        )

        while pending_pages:
            if max_pages and len(visited) >= max_pages:
                break

            remaining = sorted(
                page for page in pending_pages if page not in visited
            )
            if not remaining:
                break

            page_number = remaining[0]
            page_url = pending_pages[page_number]
            visited.add(page_number)

            _, page_html = self._request(
                page_url,
                referer=page1_url,
            )
            if self._timeout_page(page_html):
                raise RuntimeError(
                    f"Butler pagination page {page_number} hit CIC SQL timeout"
                )

            merge_page(page_html, page_url)

            for discovered_page, discovered_url in self.pagination_links(
                page_html,
                page_url,
            ).items():
                if discovered_page not in visited:
                    pending_pages.setdefault(discovered_page, discovered_url)

            if page_number == 2 or page_number % 10 == 0:
                print(
                    f"[Butler discovery] page={page_number} "
                    f"unique_statements={len(statements)}"
                )

            if sleep_seconds > 0:
                time.sleep(sleep_seconds)

        return list(statements.values()), {
            "pages_scanned": len(visited),
            "statements_discovered": len(statements),
        }

    def tax_statuses(
        self,
        statement: ButlerStatement,
    ) -> dict[int, TaxYearStatus]:
        statuses: dict[int, TaxYearStatus] = {}

        if statement.tax_history_url:
            _, history_html = self._request(
                statement.tax_history_url,
                referer=statement.result_url,
            )
            statuses.update(parse_tax_year_table(history_html))

        if statement.current_tax_url:
            _, current_html = self._request(
                statement.current_tax_url,
                referer=statement.result_url,
            )
            statuses.update(parse_tax_year_table(current_html))

        return statuses

    def appraiser_html(self, pidno: str) -> tuple[str, str]:
        url = BUTLER_APPRAISER_URL.format(pidno=pidno)
        final_url, html = self._request(url)
        if pidno not in re.sub(r"\D", "", html):
            raise RuntimeError(
                f"Butler appraiser identity could not be verified for {pidno}"
            )
        return final_url, html


def _gis_fetch_json(url: str, *, timeout: int = 45) -> dict:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with build_opener().open(request, timeout=timeout) as response:
        import json
        payload = json.loads(
            response.read().decode("utf-8", errors="replace")
        )
    if payload.get("error"):
        raise RuntimeError(payload["error"])
    return payload


def fetch_butler_gis(
    pidnos: Iterable[str],
    *,
    batch_size: int = 100,
) -> tuple[dict[str, dict], dict[str, int]]:
    wanted = sorted({
        re.sub(r"\D", "", str(pid))
        for pid in pidnos
        if len(re.sub(r"\D", "", str(pid))) == 16
    })
    result: dict[str, dict] = {}
    batches = 0

    fields = (
        "PID,PARCELNUM,PID_ORION,QuickRefID,TaxUnit,Owner,"
        "Situs_No,Situs_Dir,Situs_St,Situs_Type,Situs_Sfx,"
        "Situs_City,Situs_Zip,Class,BldgOnly"
    )

    for start in range(0, len(wanted), batch_size):
        batch = wanted[start:start + batch_size]
        if not batch:
            continue

        quoted = ",".join("'" + pid.replace("'", "''") + "'" for pid in batch)
        params = {
            "where": f"PID IN ({quoted})",
            "outFields": fields,
            "returnGeometry": "false",
            "f": "json",
        }
        payload = _gis_fetch_json(
            BUTLER_GIS_QUERY + "?" + urlencode(params)
        )
        batches += 1

        for feature in payload.get("features", []):
            attrs = feature.get("attributes") or {}
            pid = re.sub(r"\D", "", str(attrs.get("PID") or ""))
            if pid:
                result[pid] = attrs

    return result, {
        "gis_requested": len(wanted),
        "gis_matched": len(result),
        "gis_batches": batches,
    }


def _gis_address(attrs: dict) -> str:
    number = str(attrs.get("Situs_No") or "").strip()
    if number == "00000":
        number = ""
    return " ".join(
        value
        for value in (
            number,
            str(attrs.get("Situs_Dir") or "").strip(),
            str(attrs.get("Situs_St") or "").strip(),
            str(attrs.get("Situs_Type") or "").strip(),
            str(attrs.get("Situs_Sfx") or "").strip(),
        )
        if value
    )


def collect_butler_records(
    *,
    min_years: int = 2,
    max_value: float = 130_000,
    max_pages: int = 0,
    limit: int = 0,
    sleep_seconds: float = 0.10,
    client: ButlerClient | None = None,
) -> tuple[list[TaxRecord], dict[str, int]]:
    client = client or ButlerClient()

    statements, discovery_audit = client.discover_due_statements(
        max_pages=max_pages,
        sleep_seconds=max(0.0, min(sleep_seconds, 0.25)),
    )

    if limit > 0:
        statements = statements[:limit]

    verified: list[tuple[ButlerStatement, tuple[int, ...], float]] = []

    audit = {
        **discovery_audit,
        "statements_selected": len(statements),
        "tax_verified": 0,
        "tax_errors": 0,
        "currently_due": 0,
        "verified_2plus_years": 0,
        "appraiser_verified": 0,
        "appraiser_errors": 0,
        "residential_verified": 0,
        "value_le_max": 0,
    }

    total = len(statements)

    for index, statement in enumerate(statements, 1):
        try:
            statuses = client.tax_statuses(statement)
            audit["tax_verified"] += 1
        except Exception as exc:
            audit["tax_errors"] += 1
            print(
                f"[Butler tax error] {statement.pidno} "
                f"{type(exc).__name__}: {exc}"
            )
            continue

        years = unpaid_tax_years(statuses)
        if years:
            audit["currently_due"] += 1

        if len(years) >= min_years:
            audit["verified_2plus_years"] += 1
            verified.append(
                (
                    statement,
                    years,
                    displayed_total_due(statuses, years),
                )
            )

        if index == 1 or index % 25 == 0 or index == total:
            print(
                f"[Butler verify] {index}/{total} "
                f"2plus={audit['verified_2plus_years']} "
                f"errors={audit['tax_errors']}"
            )

        if sleep_seconds > 0:
            time.sleep(sleep_seconds)

    gis_by_pid, gis_audit = fetch_butler_gis(
        statement.pidno for statement, _, _ in verified
    )
    audit.update(gis_audit)

    records: list[TaxRecord] = []

    for index, (statement, years, amount_due) in enumerate(verified, 1):
        attrs = gis_by_pid.get(statement.pidno, {})

        try:
            appraiser_url, appraiser_html = client.appraiser_html(
                statement.pidno
            )
            appraisal = latest_appraisal(appraiser_html)
            if appraisal is None:
                raise RuntimeError("no appraised-values rows found")
            audit["appraiser_verified"] += 1
        except Exception as exc:
            audit["appraiser_errors"] += 1
            print(
                f"[Butler appraiser error] {statement.pidno} "
                f"{type(exc).__name__}: {exc}"
            )
            appraisal = None
            appraiser_url = BUTLER_APPRAISER_URL.format(
                pidno=statement.pidno
            )

        property_class = (
            appraisal.property_class
            if appraisal is not None
            else str(attrs.get("Class") or "")
        )
        appraised_value = (
            appraisal.total_value if appraisal is not None else None
        )
        land_value = (
            appraisal.land_value if appraisal is not None else None
        )
        improvement_value = (
            appraisal.improvement_value if appraisal is not None else None
        )

        if "RESIDENTIAL" in property_class.upper():
            audit["residential_verified"] += 1
        if appraised_value is not None and appraised_value <= max_value:
            audit["value_le_max"] += 1

        address = _gis_address(attrs) or statement.address
        city = str(attrs.get("Situs_City") or "").strip()
        zip_code = (
            str(attrs.get("Situs_Zip") or "").strip()
            or statement.zip_code
        )
        owner = (
            str(attrs.get("Owner") or "").strip()
            or statement.owner
        )

        notes = (
            "Butler CIC current-tax verified. "
            "Displayed tax balance may exclude interest, penalties, or fees; "
            "confirm payoff with Butler County Treasurer. "
            "Discovery source: paginated CIC Real Estate statements with taxes due."
        )

        source_urls = [
            statement.current_tax_url,
            statement.tax_history_url,
            appraiser_url,
        ]

        records.append(
            TaxRecord(
                county="Butler",
                parcel_id=statement.pidno,
                tax_id=statement.tax_id,
                address=address,
                city=city,
                state="KS",
                zip_code=zip_code,
                owner=owner,
                delinquent_years=years,
                amount_due=amount_due,
                appraised_value=appraised_value,
                property_class=property_class,
                status="ACTIVE",
                source_url=" | ".join(
                    dict.fromkeys(url for url in source_urls if url)
                ),
                source_type="current_tax_verified",
                notes=notes,
                ain=str(attrs.get("PID_ORION") or "").strip(),
                land_value=land_value,
                improvement_value=improvement_value,
                value_source=(
                    f"Butler Appraiser {appraisal.tax_year}"
                    if appraisal is not None
                    else ""
                ),
            )
        )

        if index % 25 == 0 or index == len(verified):
            print(
                f"[Butler appraisal] {index}/{len(verified)} "
                f"verified={audit['appraiser_verified']} "
                f"errors={audit['appraiser_errors']}"
            )

        if sleep_seconds > 0:
            time.sleep(sleep_seconds)

    return records, audit
