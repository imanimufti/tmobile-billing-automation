# T-Mobile Family Plan Automation

Fully automated, unattended T-Mobile family-plan billing. Each month it fetches
the bill from T-Mobile, splits it per line into Google Sheets, posts the
breakdown to the family WhatsApp group, tracks Venmo / Zelle / Apple Cash
payments, and DMs reminders to anyone who hasn't paid — all on a schedule,
hands-off. See **[Unattended automation](#unattended-automation)**.

## Status — live & fully automated

A macOS `launchd` agent runs the whole monthly cycle with no manual steps:

- ✅ **Fetch** the bill from T-Mobile (headless browser + seeded session; from the 20th)
- ✅ **Parse** per-line costs and build that month's Google Sheet tab
- ✅ **Announce** the breakdown to the family WhatsApp group (headless WhatsApp Web)
- ✅ **Track payments** across Venmo, Zelle, and Apple Cash — couples-aware,
  multi-month lump sums, and tolerant of small round-ups
- ✅ **Remind** anyone unpaid via private WhatsApp DM (first at 7 days, then every 5)

The only things that ever need you: an Apple Cash payment from an un-mapped
number (no name attached), a rare T-Mobile re-login if the saved session expires,
or a one-time session re-seed. See [What still needs you](#what-still-needs-you).

## Features

1. **PDF Bill Parsing** - Extracts per-line costs from T-Mobile PDF bills
2. **Google Sheets Integration** - Automatically updates your billing spreadsheet with a new tab for each month
3. **Payment Tracking** - Monitors Gmail for Venmo payment notifications and marks people as paid
4. **Smart Matching** - Matches payments using both amount and name (fuzzy matching on Venmo handles). Handles two real-world cases:
   - **Lump-sum / multi-month payments** - A single payment can settle several months at once. The matcher looks across the last few monthly tabs (`payment_match_months` in `config.json`, default 3) and finds the combination of outstanding charges that adds up to the amount paid.
   - **Couples / pays-for relationships** - When one person covers another, configure it under `pays_for` in `config.json` (e.g. `{"Qasim": ["Tuba"]}`). A payment from Qasim is matched against Qasim's *and* Tuba's outstanding charges, and clears both.

## Project Structure

```
tmobile-family-plan-automation/
├── bills/                          # Store your PDF bills here
│   └── SummaryBillMar2026.pdf
├── src/
│   ├── config.json                 # Configuration (Google Sheet ID, phone mappings)
│   ├── parse_tmobile_bill.py       # PDF parser
│   ├── update_google_sheet.py      # Google Sheets updater
│   ├── monitor_venmo_payments.py   # Gmail/Venmo/Zelle/Apple Cash payment monitor
│   ├── share_to_whatsapp.py        # Posts the breakdown to the WhatsApp group
│   ├── fetch_tmobile_bill.py       # Best-effort bill download (Playwright + Keychain)
│   ├── run_pipeline.py             # Unattended orchestrator (run by launchd)
│   └── organize_drive.py           # One-off: file the sheet under a Drive folder
├── whatsapp/                       # Headless WhatsApp Web sender (Node)
│   └── send.js                     # seed / send / send-batch / list-members
├── launchd/                        # launchd agent template
├── scripts/                        # install/uninstall the launchd agent
├── credentials.json                # Google API credentials (you need to create this)
└── README.md
```

## Unattended automation

`src/run_pipeline.py` runs the whole monthly cycle hands-off. A macOS `launchd`
agent fires it ~3×/day (08:00 / 13:00 / 19:00); each run does one idempotent pass:

1. **Acquire** – T-Mobile issues the bill on the 19th, so from the **20th**
   onward (config `tmobile.fetch_day`), if this month's
   `bills/SummaryBill<Mon><YYYY>.pdf` is missing, it downloads it (≤ once/day
   until it has it). Before the 20th it skips fetching; the other stages still run.
2. **Process** – parses the PDF and builds the month's Sheet tab (exactly once;
   it never overwrites an existing tab, so recorded payments are safe).
3. **Announce** – posts the breakdown (image + caption) to the family WhatsApp
   group via a **headless WhatsApp Web session** (`whatsapp/send.js`), so it
   works even while the Mac is locked. Requires a one-time QR seed (below).
4. **Monitor** – matches incoming Venmo/Zelle/Apple Cash payments and marks
   people Paid. Runs every pass, even while the screen is locked.
5. **Remind** – DMs anyone still unpaid past the threshold (default: first at 7
   days, then every 5 days until Paid), via WhatsApp Web. Respects the couples
   rule (a dependent's reminder rolls into the responsible payer's DM). Numbers
   live in `config.json` under `reminders.whatsapp_numbers`; toggle with
   `reminders.enabled`. Last-reminded timestamps are tracked in state so nobody
   is spammed each run.

Per-month progress lives in `state/pipeline_state.json`, so re-runs are safe.
If the T-Mobile fetch fails, you get a macOS notification and just drop the PDF
in `bills/` — the next run continues automatically. Preview a run without
changing anything: `python3 src/run_pipeline.py --dry-run`.

### One-time setup

The fragile/secure bits can't be scripted with your secrets — do these once:

```bash
# 1. T-Mobile credentials into the Keychain (two items, one service)
security add-generic-password -s tmobile-login -a username -w '<T-Mobile username>'
security add-generic-password -s tmobile-login -a password -w '<T-Mobile password>'

# 2. Playwright browser for the bill download
pip install playwright && python3 -m playwright install chromium

# 3. Seed a logged-in browser profile (do the login/2FA in the window once;
#    cookies persist so later headless runs usually skip 2FA)
python3 src/fetch_tmobile_bill.py --headed --dry-run

# 4. Seed the WhatsApp Web session (one-time QR scan) for unattended posting
cd whatsapp && npm install && node send.js seed   # scan the QR in WhatsApp → Linked Devices
node send.js list-groups                           # copy the family group's exact name
#  -> put that name in config.json under whatsapp.group_name

# 5. Install the scheduler
./scripts/install_launchd.sh        # prints the interpreter path to grant below
```

Then grant the **interpreter path the installer prints** two permissions in
**System Settings → Privacy & Security**:
- **Full Disk Access** – so it can read `~/Library/Messages/chat.db` (Zelle /
  Apple Cash payments and the 2FA code).
- **Accessibility** – so it can drive WhatsApp Desktop to send the breakdown.

Run it immediately and watch the log:
```bash
launchctl kickstart -k gui/$(id -u)/com.imani.tmobile-pipeline
tail -f logs/pipeline.log
```

Optional (since the Mac sleeps), wake it for the morning run early each month:
```bash
sudo pmset repeat wakeorpoweron MTWRFSU 07:55:00
```

To stop the automation: `./scripts/uninstall_launchd.sh`. The selectors/URLs the
T-Mobile fetcher uses live in `config.json` under `tmobile` so they can be
re-tuned without code changes if the site moves things around.

## What still needs you

In a normal month where everyone pays by Venmo/Zelle, your involvement is zero.
The exceptions are inherent, not bugs:

- **Apple Cash from an un-mapped number** — Apple Cash carries only a phone
  number, no name, so it can't auto-attribute until you map the number in
  `sms.sms_payer_aliases`. (Venmo and Zelle include names, so they're fine.)
- **An odd amount** — a payment that's *less* than owed, or matches no
  combination even with the round-up window, stays Pending for you to review.
- **A re-seed** — the WhatsApp Web or T-Mobile browser session can expire
  occasionally; re-run the relevant seed command (above). If a T-Mobile re-login
  hits the passkey, you get a notification and can drop the PDF in `bills/`
  instead — the pipeline continues either way.
- **Plan changes** — someone joins/leaves or changes number → update
  `config.json` (name mapping + `reminders.whatsapp_numbers`).
- **The Mac must be awake & logged in** for scheduled runs (sessions + Full Disk
  Access are already set). Optional: `sudo pmset repeat wakeorpoweron MTWRFSU 07:55:00`.

## Manual / override commands

The pipeline runs everything automatically, but each step can be run by hand:

```bash
python3 src/run_pipeline.py --dry-run         # preview a full run, change nothing
python3 src/run_pipeline.py                    # run all stages now
python3 src/update_google_sheet.py bills/<bill>.pdf   # just parse + build the sheet
python3 src/monitor_venmo_payments.py --days 30       # just re-check payments
python3 src/run_pipeline.py --remind-tab "Apr 26"     # send reminders for a month
```

## Setup

### 1. Install Python Dependencies

The scripts will auto-install dependencies, but you can also install manually:

```bash
pip install pymupdf google-auth google-auth-oauthlib google-auth-httplib2 google-api-python-client
```

### 2. Set Up Google API Credentials

#### Option A: OAuth 2.0 (Recommended for personal use)

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project (or select existing)
3. Enable APIs:
   - Google Sheets API
   - Gmail API
4. Go to "Credentials" → "Create Credentials" → "OAuth client ID"
5. Choose "Desktop app" as application type
6. Download the credentials and save as `credentials.json` in the project root

#### Option B: Service Account (For automated/server use)

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project
3. Enable Google Sheets API and Gmail API
4. Go to "Credentials" → "Create Credentials" → "Service Account"
5. Download the JSON key file and save as `credentials.json`
6. Share your Google Sheet with the service account email

### 3. Configure Phone Mappings

Edit `src/config.json` to map phone numbers (last 4 digits) to names:

```json
{
  "google_sheet_id": "YOUR_SHEET_ID",
  "phone_to_name_mapping": {
    "3033": "Ahmad",
    "4321": "Iqra",
    "7696": "Qasim"
  }
}
```

## Usage

### Step 1: Parse Bill and Update Google Sheet

```bash
# Parse PDF and update Google Sheet
python3 src/update_google_sheet.py bills/SummaryBillMar2026.pdf
```

This will:
- Parse the T-Mobile PDF
- Extract per-line costs (equal portion + equipment + extras)
- Create/update a tab named "Mar 26" in your Google Sheet
- Set all payment statuses to "Pending"

### Step 1b (Optional): Share Breakdown to WhatsApp

```bash
# Dry-run: print the message and URL without touching clipboard or opening WhatsApp
python3 src/share_to_whatsapp.py "Mar 26" --dry-run

# Real run: copy message to clipboard, open WhatsApp Desktop
python3 src/share_to_whatsapp.py "Mar 26"
```

This reads the bill total from cell `K1` of the target tab and builds a gid-anchored URL pointing straight at it, then drops a ready-to-send message on your clipboard. Configure the message in `src/config.json` under the `whatsapp` block:

```json
"whatsapp": {
  "group_invite_url": "",
  "payment_methods": {
    "Venmo": "@imani-mufti",
    "Zelle / Apple Pay": "2134255760"
  },
  "message_template": "T-Mobile bill is up for {tab_name}: ${total}\nYour breakdown: {sheet_url}\n\nPay via:\n{payment_methods}"
}
```

- `group_invite_url` — optional. Paste your group's invite link (Group settings → Invite via link → Copy link) for a one-click deep-link into the group. Leave blank to land in WhatsApp's main window and pick the group manually.
- `payment_methods` — dict of `label → handle`. Rendered as `• <label>: <handle>` bullets and substituted for `{payment_methods}` in the template.

### Step 2: Monitor Venmo Payments

```bash
# One-time check for payments
python3 src/monitor_venmo_payments.py "Mar 26" --days 7

# Watch mode (continuous monitoring every 5 minutes)
python3 src/monitor_venmo_payments.py "Mar 26" --watch --interval 300
```

This will:
- Search Gmail for Venmo payment notifications (last 7 days)
- Extract payer name/handle and amount
- Match to people in the sheet using:
  - **Amount match**: Payment amount = Total per person
  - **Name match**: Person's name appears in Venmo handle (case-insensitive, like `%waleed%`)
- Update "Payment Status" to "Paid" when matched

### Step 3: Parse Bill Only (Optional)

```bash
# Just parse and display summary
python3 src/parse_tmobile_bill.py bills/SummaryBillMar2026.pdf
```

## Workflow Example

```bash
# 1. Place new bill in bills/ folder
cp ~/Downloads/SummaryBillApr2026.pdf bills/

# 2. Update Google Sheet
python3 src/update_google_sheet.py bills/SummaryBillApr2026.pdf

# 3. Monitor payments (run in background or cron job)
python3 src/monitor_venmo_payments.py "Apr 26" --watch --interval 300
```

## Payment matching logic

The monitor pulls from **Venmo** (Gmail), **Zelle**, and **Apple Cash** (macOS
Messages `chat.db`), and matches each payment to a person by name + amount:

1. **Name** — the sender's name must contain the person's sheet name
   (case-insensitive). Apple Cash carries no name, so it's matched by a mapped
   number (`sms.sms_payer_aliases`).
2. **Amount** — the payment must cover the charge(s) owed, tolerating a small
   **round-up** (`payment_overpay_tolerance`, default $1) — e.g. $46.46 paid as
   $46.50 still clears. Underpayments stay Pending on purpose.

It also handles the messy real-world cases:

- **Couples** (`pays_for`) — when Qasim pays, Tuba's share is cleared too, and a
  reminder for Tuba's share is rolled into Qasim's DM.
- **Multi-month lump sums** — one payment covering several months is matched by
  finding the combination of outstanding charges that sums to it (within the
  round-up window); `payment_match_months` controls how far back to look.

Already-paid rows are skipped, so every run is safe to repeat.

Scheduling is handled by the launchd agent (see
[Unattended automation](#unattended-automation)) — no cron needed.

## Troubleshooting

### "Credentials file not found"
- Make sure `credentials.json` is in the project root
- Follow setup instructions above to create credentials

### "No match found" for payment
- Check that the person's name in `config.json` matches their Venmo handle
- Check that the payment amount matches exactly
- Run with `--days 30` to search further back

### "Permission denied" on Google Sheet
- Make sure the Google Sheet is shared with your Google account
- For service accounts, share the sheet with the service account email

## Contributing

Feel free to add features:
- WhatsApp/SMS notifications
- Automatic bill download from T-Mobile
- Web dashboard
- Multiple bill support

## License

MIT
