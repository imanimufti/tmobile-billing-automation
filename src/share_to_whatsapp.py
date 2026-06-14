#!/usr/bin/env python3
"""
Stage 5 — Share the breakdown link to WhatsApp.

Reads the bill total for a given month tab from the Google Sheet, builds a
gid-anchored URL pointing straight at that tab, formats a message from the
configured template (including payment-method bullets), copies it to the
macOS clipboard, and opens WhatsApp Desktop so you can paste + send.
"""

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    from google.oauth2.credentials import Credentials
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
except ImportError:
    print("Google API libraries not installed. Installing...")
    subprocess.check_call([sys.executable, "-m", "pip", "install",
                           "google-auth", "google-auth-oauthlib",
                           "google-auth-httplib2", "google-api-python-client"])
    from google.oauth2.credentials import Credentials
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    print("Pillow not installed. Installing...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "Pillow"])
    from PIL import Image, ImageDraw, ImageFont


SCOPES = [
    'https://www.googleapis.com/auth/gmail.readonly',
    'https://www.googleapis.com/auth/spreadsheets',
]


def authenticate(credentials_path: str = "credentials.json"):
    """Return a Sheets service. Mirrors GoogleSheetsUpdater.authenticate."""
    creds_file = Path(credentials_path)
    if not creds_file.exists():
        raise FileNotFoundError(
            f"Credentials file not found: {credentials_path}\nSee README for setup steps."
        )

    creds = None
    try:
        creds = service_account.Credentials.from_service_account_file(
            credentials_path, scopes=SCOPES)
        print("Authenticated using service account")
    except Exception:
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.auth.transport.requests import Request

        token_path = Path("token.json")
        if token_path.exists():
            creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(credentials_path, SCOPES)
                creds = flow.run_local_server(port=0)
            with open(token_path, 'w') as token:
                token.write(creds.to_json())
        print("Authenticated using OAuth")

    return build('sheets', 'v4', credentials=creds)


def fetch_tab_data(sheets, spreadsheet_id: str, tab_name: str) -> Dict:
    """Return gid, bill total, others-owe amount, and all data rows for the tab."""
    meta = sheets.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    gid = next(
        (s['properties']['sheetId']
         for s in meta.get('sheets', [])
         if s['properties']['title'] == tab_name),
        None,
    )
    if gid is None:
        raise ValueError(f"Tab '{tab_name}' not found in spreadsheet")

    result = sheets.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id, range=f"{tab_name}!A1:L25"
    ).execute()
    rows = result.get('values', [])
    if not rows:
        raise ValueError(f"Tab '{tab_name}' has no data — did Stage 2 run for it?")

    # Summary cells live in column L of the first two rows
    bill_total_raw = rows[0][11] if len(rows[0]) > 11 else ''
    others_owe_raw = rows[1][11] if len(rows) > 1 and len(rows[1]) > 11 else ''

    return {
        'gid': gid,
        'bill_total': bill_total_raw.replace('$', '').replace(',', '').strip(),
        'bill_total_display': bill_total_raw,
        'others_owe_display': others_owe_raw,
        'rows': rows,
    }


def render_sheet_as_png(rows: List[List[str]], tab_name: str,
                        bill_total_display: str, others_owe_display: str,
                        output_path: str) -> None:
    """Render the per-person breakdown table as a PNG suitable for sharing."""
    # (label, source column index in A:L, pixel width)
    columns = [
        ('Name',             0, 180),
        ('Equal Portion',    2, 130),
        ('Recurring Extras', 3, 160),
        ('Extras',           4, 100),
        ('Total',            6, 110),
        ('Status',           7, 100),
    ]

    # Filter to renderable data rows: drop header (row 0), drop the 'Total' summary row
    data_rows: List[List[str]] = []
    for row in rows[1:]:
        if not row or row[0] == 'Total':
            continue
        padded = list(row) + [''] * (12 - len(row))
        data_rows.append([padded[idx] for _, idx, _ in columns])

    table_width = sum(w for _, _, w in columns)
    margin = 20
    width = table_width + margin * 2
    row_height = 38
    title_area = 90
    table_header_height = 44
    height = title_area + table_header_height + len(data_rows) * row_height + margin

    img = Image.new('RGB', (width, height), color='white')
    draw = ImageDraw.Draw(img)

    font_path = '/System/Library/Fonts/Helvetica.ttc'
    title_font = ImageFont.truetype(font_path, 22)
    subtitle_font = ImageFont.truetype(font_path, 15)
    header_font = ImageFont.truetype(font_path, 13)
    body_font = ImageFont.truetype(font_path, 13)

    draw.text((margin, 15), f"T-Mobile Family Plan — {tab_name}",
              fill='#1a1a1a', font=title_font)
    summary_bits = []
    if bill_total_display:
        summary_bits.append(f"Bill Total: {bill_total_display}")
    if others_owe_display:
        summary_bits.append(f"Others Owe: {others_owe_display}")
    draw.text((margin, 50), '    '.join(summary_bits),
              fill='#555', font=subtitle_font)

    # Table header
    y = title_area
    draw.rectangle([margin, y, margin + table_width, y + table_header_height],
                   fill='#e8e8e8')
    x = margin
    for label, _, col_w in columns:
        draw.text((x + 10, y + 14), label, fill='#222', font=header_font)
        x += col_w
    draw.line([margin, y + table_header_height,
               margin + table_width, y + table_header_height],
              fill='#bbb', width=1)

    # Data rows
    y += table_header_height
    for i, row_data in enumerate(data_rows):
        bg = '#fafafa' if i % 2 == 0 else 'white'
        draw.rectangle([margin, y, margin + table_width, y + row_height], fill=bg)
        x = margin
        for j, (label, _, col_w) in enumerate(columns):
            text = row_data[j] if j < len(row_data) else ''
            color = '#1a1a1a'
            if label == 'Status':
                lower = text.lower()
                if lower == 'paid':
                    color = '#1e7d2c'
                elif lower == 'pending':
                    color = '#b06800'
            draw.text((x + 10, y + 11), text, fill=color, font=body_font)
            x += col_w
        y += row_height

    draw.rectangle([margin, title_area, margin + table_width, y],
                   outline='#bbb', width=1)

    img.save(output_path)


def copy_image_to_clipboard(image_path: str) -> None:
    """Copy a PNG image to the macOS clipboard so ⌘V pastes it into apps."""
    abs_path = str(Path(image_path).resolve())
    script = f'set the clipboard to (read (POSIX file "{abs_path}") as «class PNGf»)'
    subprocess.run(['osascript', '-e', script], check=True)


def render_payment_methods(methods: Dict[str, str]) -> str:
    return "\n".join(f"• {label}: {handle}" for label, handle in methods.items())


def build_message(template: str, tab_name: str, total: str,
                  sheet_url: str, methods: Dict[str, str]) -> str:
    return template.format(
        tab_name=tab_name,
        total=total,
        sheet_url=sheet_url,
        payment_methods=render_payment_methods(methods),
    )


def copy_to_clipboard(text: str) -> None:
    proc = subprocess.run(['pbcopy'], input=text.encode('utf-8'), check=True)
    if proc.returncode != 0:
        raise RuntimeError("pbcopy failed")


def open_whatsapp(group_invite_url: Optional[str]) -> None:
    if group_invite_url:
        subprocess.run(['open', group_invite_url], check=True)
    else:
        subprocess.run(['open', '-a', 'WhatsApp'], check=True)


def send_via_applescript(open_delay: float = 4.0, after_paste_delay: float = 1.0) -> None:
    """Drive WhatsApp Desktop to paste from clipboard and press Enter.

    Requires Accessibility permission for the terminal/process running this
    script (System Settings → Privacy & Security → Accessibility). The first
    run will surface a permission prompt; subsequent runs are silent.
    """
    script = f'''
    tell application "WhatsApp" to activate
    delay {open_delay}
    tell application "System Events"
        keystroke "v" using {{command down}}
        delay {after_paste_delay}
        keystroke return
    end tell
    '''
    subprocess.run(['osascript', '-e', script], check=True)


def main():
    parser = argparse.ArgumentParser(
        description='Share the Google Sheet breakdown for a given month to WhatsApp')
    parser.add_argument('tab_name', help='Sheet tab name (e.g. "Mar 26")')
    parser.add_argument('--credentials', default='credentials.json',
                        help='Path to credentials.json')
    parser.add_argument('--config', default='src/config.json',
                        help='Path to config.json')
    parser.add_argument('--dry-run', action='store_true',
                        help='Print the rendered message + URL only; do not touch clipboard or open WhatsApp')
    parser.add_argument('--no-send', action='store_true',
                        help='Open WhatsApp and copy each artifact to clipboard, but skip the auto-paste/Enter')
    parser.add_argument('--no-screenshot', action='store_true',
                        help='Skip the PNG breakdown image — send the text link message only')
    parser.add_argument('--render-only', action='store_true',
                        help='Render the PNG breakdown to a temp file and exit. Prints the path.')
    parser.add_argument('--open-delay', type=float, default=4.0,
                        help='Seconds to wait after launching WhatsApp before the first paste (default: 4.0)')
    args = parser.parse_args()

    with open(args.config, 'r') as f:
        config = json.load(f)

    spreadsheet_id = config['google_sheet_id']
    wa = config.get('whatsapp', {})
    template = wa.get('message_template')
    methods = wa.get('payment_methods', {})
    group_invite_url = wa.get('group_invite_url') or None

    if not template:
        print("Error: whatsapp.message_template is missing from config.json")
        sys.exit(1)

    sheets = authenticate(args.credentials)
    data = fetch_tab_data(sheets, spreadsheet_id, args.tab_name)
    sheet_url = f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit#gid={data['gid']}"

    message = build_message(template, args.tab_name, data['bill_total'], sheet_url, methods)

    print("\n" + "=" * 60)
    print(message)
    print("=" * 60)

    # Render screenshot (default on)
    png_path: Optional[str] = None
    if not args.no_screenshot:
        slug = args.tab_name.replace(' ', '_')
        png_path = str(Path(tempfile.gettempdir()) / f"tmobile-{slug}.png")
        render_sheet_as_png(
            data['rows'], args.tab_name,
            data['bill_total_display'], data['others_owe_display'],
            png_path,
        )
        print(f"\n✓ Screenshot rendered: {png_path}")

    if args.render_only:
        print("[render-only] Exiting before clipboard/WhatsApp.")
        return

    if args.dry_run:
        print("\n[dry-run] Clipboard not touched, WhatsApp not opened.")
        return

    open_whatsapp(group_invite_url)
    if group_invite_url:
        print(f"✓ Opened WhatsApp into the group ({group_invite_url})")
    else:
        print("✓ Opened WhatsApp Desktop (pick the group manually)")

    # Send the screenshot first so the text-with-link follows as a separate message.
    sent_image = False
    if png_path:
        copy_image_to_clipboard(png_path)
        print("✓ Screenshot copied to clipboard")
        if args.no_send:
            print("→ Paste with ⌘V (image preview opens), then Enter to send")
            print("  After it sends, the text message will be staged next — rerun without --no-send.")
        else:
            print(f"→ Sending screenshot in ~{args.open_delay:.0f}s...")
            try:
                # Image paste opens a preview dialog; give it extra time before Enter.
                send_via_applescript(open_delay=args.open_delay, after_paste_delay=2.5)
                sent_image = True
                print("✓ Screenshot sent")
            except subprocess.CalledProcessError as e:
                print(f"✗ AppleScript send failed: {e}")
                print("  Falling back to manual paste — image is on your clipboard.")
                print("  If this is the first run, grant Accessibility permission:")
                print("    System Settings → Privacy & Security → Accessibility")
                sys.exit(1)

    copy_to_clipboard(message)
    print("✓ Text message copied to clipboard")

    if args.no_send:
        print("→ Paste with ⌘V, then Enter to send the link message")
        return

    # WhatsApp already focused on the group; only a short re-focus delay is needed.
    text_open_delay = 1.5 if sent_image else args.open_delay
    print(f"→ Sending text message in ~{text_open_delay:.1f}s...")
    try:
        send_via_applescript(open_delay=text_open_delay, after_paste_delay=1.0)
        print("✓ Text message sent")
    except subprocess.CalledProcessError as e:
        print(f"✗ AppleScript send failed: {e}")
        print("  Text message is on your clipboard — paste manually with ⌘V + Enter.")
        sys.exit(1)


if __name__ == "__main__":
    main()
