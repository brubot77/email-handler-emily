from __future__ import annotations

import datetime as dt
import re
from .models import DocumentAnalysis

MONEY = r"\$?\s*([0-9][0-9,]*\.\d{2})"


def _first(patterns: list[str], text: str, flags=re.I | re.M) -> str:
    for pattern in patterns:
        m = re.search(pattern, text, flags)
        if m:
            return re.sub(r"\s+", " ", m.group(1)).strip(" :")
    return ""


def _money(labels: list[str], text: str) -> str:
    for label in labels:
        m = re.search(rf"{label}[^\n$]{{0,80}}{MONEY}", text, re.I)
        if m:
            return "$" + m.group(1).replace(" ", "")
    return ""


def _parse_date(value: str) -> str:
    value = re.sub(r"\s+", " ", value.strip())
    formats = ["%m/%d/%Y", "%m/%d/%y", "%B %d, %Y", "%b %d, %Y", "%Y-%m-%d"]
    for fmt in formats:
        try:
            return dt.datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            pass
    return ""


def extract_transaction_date(text: str) -> tuple[str, int, str]:
    priorities = [
        ("Closing Date", 100), ("Settlement Date", 98), ("Disbursement Date", 94),
        ("Funding Date", 92), ("Consummation Date", 90), ("Effective Date", 75),
    ]
    date_pattern = r"([A-Za-z]{3,9}\s+\d{1,2},\s+\d{4}|\d{1,2}/\d{1,2}/\d{2,4}|\d{4}-\d{2}-\d{2})"
    for label, confidence in priorities:
        m = re.search(rf"{re.escape(label)}\s*:?\s*{date_pattern}", text, re.I)
        if m:
            parsed = _parse_date(m.group(1))
            if parsed:
                return parsed, confidence, label
    return "", 0, "No transaction date found"


def extract_addresses(text: str) -> tuple[list[str], dict[str, str]]:
    candidates: list[tuple[str, int]] = []
    labels = ["Property Address", "Subject Property", "Property", "Premises"]
    for label in labels:
        for m in re.finditer(rf"{label}\s*:?\s*([^\n\r]+)", text, re.I):
            raw = re.sub(r"\s+", " ", m.group(1)).strip()
            # Address tokens beginning with a street number; support comma lists sharing city/state.
            found = re.findall(r"\b\d{1,6}\s+(?:[NSEW]\.?\s+)?[A-Za-z0-9.'-]+(?:\s+[A-Za-z0-9.'-]+){0,6}(?:\s+(?:St|Street|Ave|Avenue|Rd|Road|Dr|Drive|Ln|Lane|Ct|Court|Blvd|Boulevard|Pl|Place|Ter|Terrace|Cir|Circle|Hwy|Highway))?\b", raw, re.I)
            for item in found:
                candidates.append((re.sub(r"\s+", " ", item).strip(" ,"), m.start()))
    # Broad fallback, limited to lines containing property-like labels.
    if not candidates:
        for line_no, line in enumerate(text.splitlines(), start=1):
            if re.search(r"property|subject|premises", line, re.I):
                for item in re.findall(r"\b\d{1,6}\s+(?:[NSEW]\.?\s+)?[A-Za-z0-9.'-]+(?:\s+[A-Za-z0-9.'-]+){0,5}\b", line):
                    candidates.append((item.strip(), line_no))
    unique: list[str] = []
    refs: dict[str, str] = {}
    for address, pos in candidates:
        key = address.lower()
        if key not in {a.lower() for a in unique}:
            unique.append(address)
            page = text[:pos].count("\f") + 1
            refs[address] = str(page)
    return unique, refs


def classify_document(text: str) -> tuple[str, str, int, str]:
    lower = text.lower()
    subtype = ""
    if "closing disclosure" in lower:
        subtype = "Closing Disclosure"
    elif "alta settlement statement" in lower or "alta combined settlement" in lower:
        subtype = "ALTA Settlement Statement"
    elif "hud-1" in lower or "hud 1" in lower:
        subtype = "HUD-1"
    elif "settlement statement" in lower:
        subtype = "Settlement Statement"

    refi_score = sum(1 for term in ["refinance", "cash-out", "cash out", "existing loan payoff", "payoff of first mortgage", "new loan amount", "due to buyer/borrower"] if term in lower)
    acquisition_score = sum(1 for term in ["contract sales price", "purchase price", "seller", "buyer", "earnest money", "acquisition"] if term in lower)
    sale_score = sum(1 for term in ["seller proceeds", "due to seller", "real estate commission", "seller closing statement"] if term in lower)

    if refi_score and acquisition_score >= 3:
        return "Combined Closing Package", subtype, 82, f"Refinance indicators={refi_score}; acquisition indicators={acquisition_score}"
    if refi_score >= 2:
        return "Refinance Closing Statement", subtype, min(98, 78 + refi_score * 4), f"Found {refi_score} refinance indicators"
    if sale_score >= 2:
        return "Sale Closing Statement", subtype, min(96, 78 + sale_score * 5), f"Found {sale_score} sale indicators"
    if acquisition_score >= 2:
        return "Acquisition Closing Statement", subtype, min(96, 76 + acquisition_score * 4), f"Found {acquisition_score} acquisition indicators"
    if subtype:
        return "Closing Statement", subtype, 70, f"Recognized {subtype}, but transaction purpose is unclear"
    return "Unknown", "", 35, "No strong closing or refinance indicators"


def analyze_document(text: str) -> DocumentAnalysis:
    document_type, subtype, confidence, reason = classify_document(text)
    transaction_date, date_confidence, date_reason = extract_transaction_date(text)
    addresses, refs = extract_addresses(text)
    lender = _first([r"^Lender\s*:\s*(.+)$", r"^Creditor\s*:\s*(.+)$"], text)
    title_company = _first([r"^Settlement Agent\s*:\s*(.+)$", r"^Title Company\s*:\s*(.+)$"], text)
    borrower = _first([r"^(?:Buyer|Borrower)\s*:\s*(.+)$"], text)
    return DocumentAnalysis(
        document_type=document_type,
        document_subtype=subtype,
        transaction_date=transaction_date,
        date_confidence=date_confidence,
        lender=lender,
        title_company=title_company,
        borrower=borrower,
        addresses=addresses,
        classification_confidence=confidence,
        classification_reason=reason + "; " + date_reason,
        purchase_price=_money(["Contract Sales Price", "Purchase Price"], text),
        new_loan_amount=_money(["New Loan Amount", "Loan Amount"], text),
        prior_loan_payoff=_money(["Existing Loan Payoff", "Payoff of First Mortgage", "Loan Payoff"], text),
        cash_to_borrower=_money(["Due to Buyer/Borrower", "Cash to Borrower", "Cash From/To Borrower"], text),
        interest_rate=_first([r"Interest Rate\s*:?\s*([0-9.]+\s*%)"], text),
        loan_term=_first([r"Loan Term\s*:?\s*([^\n]+)"], text),
        page_references=refs,
        raw_text=text,
    )
