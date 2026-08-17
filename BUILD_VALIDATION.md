# Build validation

Validated on 2026-08-16 before packaging:

- All Python source files compile with `python -m compileall`.
- Canonical address-key logic was exercised, including preservation of N/S/E/W directionals.
- DOCX generation was exercised successfully and the generated report was rendered for visual review.
- The integration installer was run twice against the current Emily/Morgan anchor pattern to verify idempotency; it inserts one import and one handler block only.
- The Drive completion workflow uses a non-`.docx` staging upload, writes/updates the summary row, and only then renames the file to the final `.docx`. A final Word document therefore remains the completion/source-of-truth marker.
- The current OpenAI Responses API request shape was checked against official OpenAI documentation for `gpt-5.6`, built-in `web_search`, approximate `user_location`, and strict JSON-schema structured output.

Not performed in the build container:

- A live OpenAI API call (the build container does not contain the official `openai` Python package or the user's API key).
- A live execution through the user's VPS Python virtual environment.

Those two checks are intentionally covered by the one-property smoke test in `APPRAISAL_AGENT_SETUP.md` after `pip install -r requirements.txt` and VPS `.env` configuration.
