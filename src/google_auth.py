"""One place that decides which Google credentials the scripts use.

Preference order:
  1. A service account key at state/service_account.json. It never expires and
     needs no browser. Share the Google Sheet with the account's email (Editor)
     and it can read and write the sheet and export it through Drive.
  2. The user OAuth token (token.json / credentials.json). Google can revoke a
     user token at any time (password change, inactivity, security event), and
     it has broken this pipeline twice. Only Gmail still needs it, because the
     Gmail API cannot use a service account on a personal Google account.

Use `sheets_drive_credentials()` for Sheets and Drive, and
`gmail_credentials()` for Gmail. The Gmail one returns None when no usable
user token exists; callers must treat Gmail as optional.
"""
import os
from pathlib import Path
from typing import Optional

from google.oauth2 import service_account
from google.oauth2.credentials import Credentials

from google_scopes import GOOGLE_SCOPES

ROOT = Path(__file__).resolve().parent.parent
SERVICE_ACCOUNT_FILE = ROOT / "state" / "service_account.json"
TOKEN_FILE = ROOT / "token.json"
CLIENT_FILE = ROOT / "credentials.json"

SHEETS_DRIVE_SCOPES = [s for s in GOOGLE_SCOPES if "gmail" not in s]


def service_account_available() -> bool:
    return SERVICE_ACCOUNT_FILE.exists()


def _user_credentials(interactive: bool):
    """The OAuth user token, refreshed if needed. With interactive=True, run the
    browser consent flow when there is no usable token; otherwise return None."""
    from google.auth.transport.requests import Request
    creds = None
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), GOOGLE_SCOPES)
    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            TOKEN_FILE.write_text(creds.to_json())
            return creds
        except Exception as err:  # revoked / invalid_grant
            if not interactive:
                print(f"[google-auth] user token refresh failed: {err}")
                return None
    if not interactive:
        return None
    from google_auth_oauthlib.flow import InstalledAppFlow
    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_FILE), GOOGLE_SCOPES)
    creds = flow.run_local_server(port=0)
    TOKEN_FILE.write_text(creds.to_json())
    return creds


def sheets_drive_credentials(interactive: bool = True):
    """Credentials for Sheets + Drive. Service account first; else user token."""
    if service_account_available():
        print("Authenticated using service account")
        creds = service_account.Credentials.from_service_account_file(
            str(SERVICE_ACCOUNT_FILE), scopes=SHEETS_DRIVE_SCOPES)
        # Populate .token now: the Drive PNG export uses the raw bearer token,
        # not a googleapiclient session that refreshes on its own.
        from google.auth.transport.requests import Request
        creds.refresh(Request())
        return creds
    creds = _user_credentials(interactive)
    if creds is None:
        raise RuntimeError(
            "No Google credentials: add state/service_account.json (preferred) or "
            "re-run the OAuth consent to refresh token.json")
    print("Authenticated using OAuth")
    return creds


def gmail_credentials() -> Optional[Credentials]:
    """User credentials for Gmail, or None. Never opens a browser, never raises:
    Gmail is a secondary payment source and must not block the pipeline."""
    try:
        creds = _user_credentials(interactive=False)
    except Exception as err:
        print(f"[google-auth] Gmail unavailable: {err}")
        return None
    if creds is None:
        print("[google-auth] Gmail unavailable: no valid user token (Venmo texts still cover Venmo)")
    return creds
