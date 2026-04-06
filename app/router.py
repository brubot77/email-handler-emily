from pathlib import Path


def choose_destination(filename, subject, monthly_dir, deal_dir, unmatched_dir):
    """
    Decide where an attachment should be saved based on filename + email subject.
    """

    filename_lower = filename.lower()
    subject_lower = subject.lower()

    # Monthly Analyzer routing
    if filename_lower.endswith(".pdf") and "monthly statement" in subject_lower and any(tag in lower for tag in ["blu1", "blu2"]):
        return Path(monthly_dir)

    # Deal Analyzer routing
    if filename_lower.endswith((".csv", ".xlsx", ".xls")) and "mls" in subject_lower:
        return Path(deal_dir)

    # Otherwise unmatched
    return Path(unmatched_dir)