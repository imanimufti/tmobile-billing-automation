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

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/drive",
]
FOLDER_NAME = "TMobile Automation"


def authenticate():
    token_path = Path("token.json")
    creds = None
    # Read the *granted* scopes straight from the token file — from_authorized_user_file
    # reports the requested scopes, which would falsely look complete.
    granted = set()
    if token_path.exists():
        try:
            granted = set(json.loads(token_path.read_text()).get("scopes", []))
        except Exception:
            granted = set()
        if set(SCOPES) <= granted:
            creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())

    if not creds or not creds.valid or not (set(SCOPES) <= granted):
        print("Drive access needed — a browser window will open for consent...")
        flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
        creds = flow.run_local_server(port=0)
        token_path.write_text(creds.to_json())
        print("✓ Authorized (token.json upgraded with Drive access)")
    return creds


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
