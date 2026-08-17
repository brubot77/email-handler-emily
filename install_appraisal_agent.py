from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parent
MAIN = ROOT / "app" / "main.py"
REQUIREMENTS = ROOT / "requirements.txt"
ENV_EXAMPLE = ROOT / ".env.example"

IMPORT_LINE = "from app.appraisal_agent.email_handler import handle_appraisal_request\n"
IMPORT_ANCHOR = "from app.morgan import handle_morgan_message\n"

HANDLER_BLOCK = '''        handled_appraisal = handle_appraisal_request(\n            message,\n            gmail,\n            processed_label_id,\n            failed_label_id,\n        )\n\n        if handled_appraisal:\n            processed_ids.add(message_id)\n            state.save(processed_ids)\n            continue\n\n'''
HANDLER_ANCHOR = '''        if handled_morgan:\n            processed_ids.add(message_id)\n            state.save(processed_ids)\n            continue\n'''


def patch_main() -> None:
    text = MAIN.read_text(encoding="utf-8")
    changed = False

    if IMPORT_LINE not in text:
        if IMPORT_ANCHOR not in text:
            raise RuntimeError("Could not find Morgan import anchor in app/main.py")
        text = text.replace(IMPORT_ANCHOR, IMPORT_ANCHOR + IMPORT_LINE, 1)
        changed = True

    if "handled_appraisal = handle_appraisal_request(" not in text:
        if HANDLER_ANCHOR not in text:
            raise RuntimeError("Could not find Morgan handler anchor in app/main.py")
        text = text.replace(HANDLER_ANCHOR, HANDLER_ANCHOR + "\n" + HANDLER_BLOCK, 1)
        changed = True

    if changed:
        MAIN.write_text(text, encoding="utf-8")
        print("Patched app/main.py")
    else:
        print("app/main.py already patched")


def patch_requirements() -> None:
    text = REQUIREMENTS.read_text(encoding="utf-8") if REQUIREMENTS.exists() else ""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    changed = False
    for package in ["openai", "python-docx"]:
        if package not in lines:
            lines.append(package)
            changed = True
    REQUIREMENTS.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("Updated requirements.txt" if changed else "requirements.txt already includes Appraisal Agent dependencies")


def patch_env_example() -> None:
    current = ENV_EXAMPLE.read_text(encoding="utf-8") if ENV_EXAMPLE.exists() else ""
    additions = {
        "OPENAI_API_KEY": "",
        "APPRAISAL_MODEL": "gpt-5.6",
        "APPRAISAL_REASONING_EFFORT": "high",
        "APPRAISAL_REPORT_FOLDER_ID": "1aFAe0gkV1EBsljSaiM88z6D8EcQqHZ4r",
        "ACTIVE_DEALS_SPREADSHEET_ID": "1y1ECfqxKioxOPIjJ6ce2woLlDAhjNNkiN99ggwG0XKU",
        "APPRAISAL_MAX_PER_RUN": "",
    }
    existing_keys = {line.split("=", 1)[0].strip() for line in current.splitlines() if "=" in line and not line.lstrip().startswith("#")}
    new_lines = []
    for key, value in additions.items():
        if key not in existing_keys:
            new_lines.append(f"{key}={value}")
    if new_lines:
        if current and not current.endswith("\n"):
            current += "\n"
        current += "\n# BLU Appraisal Agent\n" + "\n".join(new_lines) + "\n"
        ENV_EXAMPLE.write_text(current, encoding="utf-8")
        print("Updated .env.example")
    else:
        print(".env.example already includes Appraisal Agent settings")


if __name__ == "__main__":
    if not MAIN.exists():
        raise SystemExit("Run this script from the root of the email-handler-emily repository after extracting the bundle there.")
    patch_main()
    patch_requirements()
    patch_env_example()
    print("Appraisal Agent source integration complete.")
