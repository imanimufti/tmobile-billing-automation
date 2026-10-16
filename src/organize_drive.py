#!/usr/bin/env python3
"""
One-off: create a Drive folder and move the billing sheet into it.

Needs the Drive scope (the rest of the app only uses Sheets + Gmail), so this
re-runs the OAuth consent once and upgrades token.json to include Drive — which
also lets the announce stage auto-share the sheet going forward. Moving the
sheet keeps its same file id, link, and sharing, so family links still work.
"""

import json
import sys
from pathlib import Path

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

from google_scopes import GOOGLE_SCOPES
SCOPES = GOOGLE_SCOPES
FOLDER_NAME = "TMobile Automation"


def authenticate():
    """Service account when present (never expires), else the user OAuth token."""
    from google_auth import sheets_drive_credentials
    return sheets_drive_credentials()


def main():
    cfg = json.load(open("src/config.json"))
    sheet_id = cfg["google_sheet_id"]

    creds = authenticate()
    drive = build("drive", "v3", credentials=creds)

    # Confirm the sheet and find its current parent(s).
    f = drive.files().get(fileId=sheet_id, fields="id,name,parents").execute()
    print(f"Sheet: '{f['name']}' ({sheet_id})")

    # Find an existing folder with this name, else create it.
    q = (f"name = '{FOLDER_NAME}' and mimeType = 'application/vnd.google-apps.folder' "
         "and trashed = false")
    found = drive.files().list(q=q, fields="files(id,name)").execute().get("files", [])
    if found:
        folder_id = found[0]["id"]
        print(f"Folder '{FOLDER_NAME}' already exists ({folder_id})")
    else:
        folder = drive.files().create(
            body={"name": FOLDER_NAME, "mimeType": "application/vnd.google-apps.folder"},
            fields="id",
        ).execute()
        folder_id = folder["id"]
        print(f"✓ Created folder '{FOLDER_NAME}' ({folder_id})")

    # Move the sheet into the folder (same file id → link/sharing unchanged).
    prev_parents = ",".join(f.get("parents", []))
    if folder_id in f.get("parents", []):
        print("Sheet is already in the folder — nothing to move")
    else:
        drive.files().update(
            fileId=sheet_id, addParents=folder_id,
            removeParents=prev_parents, fields="id,parents",
        ).execute()
        print(f"✓ Moved '{f['name']}' into '{FOLDER_NAME}'")

    print(f"\nFolder: https://drive.google.com/drive/folders/{folder_id}")


if __name__ == "__main__":
    main()
