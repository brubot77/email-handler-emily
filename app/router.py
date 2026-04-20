from pathlib import Path


def choose_destination(filename, subject, monthly_dir, deal_dir, unmatched_dir):
    """
    Decide where an attachment should be saved based on filename + email subject.

    Current behavior:
    - Monthly Analyzer:
        * subject must contain "monthly statement"
        * file must be a PDF
        * filename must include one of bru1, bru2, blu1, blu2
    - Deal Analyzer (Shannon):
        * any CSV goes to Shannon input
    - Otherwise:
        * unmatched
    """

    filename_lower = str(filename or "").lower().strip()
    subject_lower = str(subject or "").lower().strip()

    monthly_tags = ["bru1", "bru2", "blu1", "blu2"]

    print(f"Router debug: filename='{filename_lower}', subject='{subject_lower}'")

    # Monthly Analyzer routing
    if (
        filename_lower.endswith(".pdf")
        and "monthly statement" in subject_lower
        and any(tag in filename_lower for tag in monthly_tags)
    ):
        print("Router debug: matched monthly route")
        return Path(monthly_dir)

    # Deal Analyzer routing
    # For now, route any CSV from an allowed sender into Shannon input.
    if filename_lower.endswith(".csv"):
        print("Router debug: matched deal route")
        return Path(deal_dir)

    # Otherwise unmatched
    print("Router debug: matched unmatched route")
    return Path(unmatched_dir)