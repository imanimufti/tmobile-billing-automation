"""Single source of truth for the Google OAuth scopes this project uses.

All scripts (sheet update, payment monitor, WhatsApp share, drive organizer)
share ONE token.json. If they request different scope subsets, whichever script
re-mints the token first after it dies narrows the others (e.g. a sheet-only
re-auth silently strips Gmail from the monitor). Import GOOGLE_SCOPES everywhere
so every re-auth keeps the full capability set.
"""

GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]
