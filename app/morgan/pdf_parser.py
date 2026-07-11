from __future__ import annotations

from pathlib import Path
from pypdf import PdfReader


def extract_pdf_pages(path: Path) -> list[str]:
    reader = PdfReader(str(path))
    pages: list[str] = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or "")
        except Exception:
            pages.append("")
    return pages


def extract_pdf_text(path: Path) -> str:
    return "\n\n".join(extract_pdf_pages(path))
