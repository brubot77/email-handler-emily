from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PropertyRecord:
    address: str
    llc: str
    canonical_key: str
    city: str = ""
    state: str = "KS"
    zip_code: str = ""
    active: str = "Yes"
    folder_id: str = ""
    folder_url: str = ""


@dataclass
class DocumentAnalysis:
    document_type: str
    document_subtype: str
    transaction_date: str
    date_confidence: int
    lender: str
    title_company: str
    borrower: str
    addresses: list[str] = field(default_factory=list)
    classification_confidence: int = 0
    classification_reason: str = ""
    purchase_price: str = ""
    new_loan_amount: str = ""
    prior_loan_payoff: str = ""
    cash_to_borrower: str = ""
    interest_rate: str = ""
    loan_term: str = ""
    page_references: dict[str, str] = field(default_factory=dict)
    raw_text: str = ""
