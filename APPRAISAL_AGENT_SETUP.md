# BLU Appraisal Agent - Installation and Deployment

## What this adds

The Appraisal Agent is a separate package inside `email-handler-emily`:

- Reads the native `BLU Active Deals - Google Sheet` / `Active Deals` tab.
- Treats every populated Address + City row as an eligible active deal.
- Uses `Address + City + State` as the canonical property identity.
- Checks the dedicated Drive folder before doing research.
- If a matching Word review already exists, the property is skipped.
- If the review is missing, the agent performs new web research, creates a comprehensive `.docx`, uploads it, and upserts one row in the summary Google Sheet.
- Deleting the Word review from Drive makes the property eligible again on the next run. The existing summary row is updated instead of duplicated.
- Appraisal and rent confidence are independent.
- 1-4 unit and 5+ unit properties use different appraisal instructions.
- Email subject `Run Appraisal Agent` (or `Appraisal Agent`) starts the same runner manually.
- A systemd timer runs the agent every day at 5:00 AM America/Chicago.

## Drive location already created

`BLU Review Docs / Property_Reviews / Appraisal_Forecasts`

Folder ID:

`1aFAe0gkV1EBsljSaiM88z6D8EcQqHZ4r`

The summary spreadsheet is created automatically in this folder on the first non-dry run and is named:

`BLU Appraisal Forecast Summary`

## Required API configuration

The agent uses the OpenAI Responses API with the built-in web-search tool. It requires an OpenAI API key in the VPS `.env` file. Do not commit the key to GitHub.

Add these entries to `.env`:

```env
OPENAI_API_KEY=YOUR_OPENAI_API_KEY
APPRAISAL_MODEL=gpt-5.6
APPRAISAL_REASONING_EFFORT=high
APPRAISAL_REPORT_FOLDER_ID=1aFAe0gkV1EBsljSaiM88z6D8EcQqHZ4r
ACTIVE_DEALS_SPREADSHEET_ID=1y1ECfqxKioxOPIjJ6ce2woLlDAhjNNkiN99ggwG0XKU
APPRAISAL_MAX_PER_RUN=
```

Leave `APPRAISAL_MAX_PER_RUN` blank for the production behavior requested: process every active property that does not already have a Word review. During testing you may temporarily set it to `1`.

## Local Windows installation

From the root of the `email-handler-emily` repository, extract this bundle so the included `app/`, `systemd/`, `tests/`, and installer files land in the repository root.

Then run:

```powershell
python install_appraisal_agent.py
.\venv\Scripts\python.exe -m pip install -r requirements.txt
.\venv\Scripts\python.exe -m compileall app
.\venv\Scripts\python.exe -m unittest tests.test_appraisal_agent -v
git status
```

The installer is idempotent. It:

1. Adds the Appraisal Agent email handler to `app/main.py` immediately after Morgan.
2. Adds `openai` and `python-docx` to `requirements.txt`.
3. Adds non-secret Appraisal Agent settings to `.env.example`.

Review the `git diff`, then use the established deployment flow:

```powershell
git add app systemd tests requirements.txt .env.example install_appraisal_agent.py APPRAISAL_AGENT_SETUP.md
git commit -m "Add BLU Appraisal Agent"
git push origin main
```

Do not add `.env` or any API key to the commit.

## VPS deployment

```bash
cd /home/brubot77/email-handler-emily
git pull origin main
source venv/bin/activate
pip install -r requirements.txt
python -m compileall app
```

Add the required variables above to `/home/brubot77/email-handler-emily/.env`.

### 1. Dry run first

This reads the live Active Deals tracker and Drive folder but creates no reports:

```bash
./venv/bin/python -m app.appraisal_agent_runner --dry-run
```

### 2. Run one property for validation

Because the Drive folder is new, the initial production queue will likely contain many properties. Validate one first:

```bash
./venv/bin/python -m app.appraisal_agent_runner --address "315 SW 2nd" --limit 1
```

Inspect:

- the new Word review in `Appraisal_Forecasts`
- the new `BLU Appraisal Forecast Summary` Google Sheet
- appraisal range and confidence
- rent range and confidence
- comparable sales/rents and source links

### 3. Test the no-duplicate rule

Run the same command again:

```bash
./venv/bin/python -m app.appraisal_agent_runner --address "315 SW 2nd" --limit 1
```

Expected result: it reports the property as `Skipped existing` and creates nothing.

### 4. Test the manual-refresh rule

Delete the Word review for 315 SW 2nd from the `Appraisal_Forecasts` Drive folder, then run the same command again. Expected result:

- a fresh Word review is created
- the existing summary row is updated
- no duplicate summary row is created

## Enable daily scheduling

After the one-property test is satisfactory:

```bash
sudo cp systemd/appraisal-agent.service /etc/systemd/system/appraisal-agent.service
sudo cp systemd/appraisal-agent.timer /etc/systemd/system/appraisal-agent.timer
sudo systemctl daemon-reload
sudo systemctl enable --now appraisal-agent.timer
systemctl list-timers --all | grep appraisal-agent
```

The timer runs daily at 5:00 AM America/Chicago and uses `Persistent=true`, so a missed run is picked up after the VPS returns.

## Enable the Emily email trigger

Restart Emily after deployment:

```bash
sudo systemctl restart emily
sudo systemctl status emily --no-pager
```

Then email Emily with subject:

`Run Appraisal Agent`

Emily starts the same Appraisal Agent runner as a detached process. If a scheduled/manual run is already active, the process lock prevents a second simultaneous run.

## Logs

Scheduled service:

```bash
journalctl -u appraisal-agent.service -n 200 --no-pager
```

Email-triggered runs:

```bash
tail -200 /home/brubot77/email-handler-emily/appraisal_agent.log
```

## Useful commands

See what would run without changing anything:

```bash
./venv/bin/python -m app.appraisal_agent_runner --dry-run
```

Run one specific address, still respecting the existing-document rule:

```bash
./venv/bin/python -m app.appraisal_agent_runner --address "216 S Duncan"
```

Run the full missing-property queue immediately:

```bash
./venv/bin/python -m app.appraisal_agent_runner
```

Check the timer:

```bash
systemctl status appraisal-agent.timer --no-pager
```

Run the scheduled service manually:

```bash
sudo systemctl start appraisal-agent.service
```

## Important behavior

There is intentionally no `--force` option. A property with an existing Word report is never re-researched. To refresh a property, delete its Word review from the Drive folder; the next scheduled or manual run will recreate it.
