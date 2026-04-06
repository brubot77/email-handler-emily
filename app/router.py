from pathlib import Path


def choose_destination(filename, subject, monthly_dir, deal_dir, unmatched_dir):
    """
    Decide where an attachment should be saved based on filename + email subject.
    """

    filename_lower = filename.lower()
    subject_lower = subject.lower()

    monthly_tags = ["bru1", "bru2", "blu1", "blu2"]

    # Monthly Analyzer routing
    if (
        ("monthly statement" in subject_lower and filename_lower.endswith(".pdf"))
        and any(tag in filename_lower for tag in monthly_tags)
    ):
        return Path(monthly_dir)

    # Deal Analyzer routing
    if (
        ("mls" in subject_lower and filename_lower.endswith((".csv", ".xlsx", ".xls")))
    ):
        return Path(deal_dir)

    # Otherwise unmatched
    return Path(unmatched_dir)