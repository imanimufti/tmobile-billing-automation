#!/usr/bin/env python3
"""
Gmail Monitor for Venmo Payment Notifications
Watches Gmail for Venmo payment emails and updates Google Sheet payment status
"""

import json
import sys
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
import time

try:
    from google.oauth2.credentials import Credentials
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    import base64
except ImportError:
    print("Google API libraries not installed. Installing...")
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "google-auth", "google-auth-oauthlib", "google-auth-httplib2", "google-api-python-client"])
    from google.oauth2.credentials import Credentials
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    import base64


class VenmoPaymentMonitor:
    """Monitors Gmail for Venmo payment notifications and updates Google Sheet"""

    def __init__(self, config_path: str = "src/config.json"):
        # Load configuration
        with open(config_path, 'r') as f:
            self.config = json.load(f)

        self.sheet_id = self.config['google_sheet_id']
        self.phone_mapping = self.config['phone_to_name_mapping']
        self.gmail_service = None
        self.sheets_service = None

    def authenticate(self, credentials_path: str = "credentials.json"):
        """Authenticate with Gmail and Google Sheets APIs"""
        # Need both Gmail and Sheets scopes
        SCOPES = [
            'https://www.googleapis.com/auth/gmail.readonly',
            'https://www.googleapis.com/auth/spreadsheets'
        ]

        creds = None
        creds_file = Path(credentials_path)

        if creds_file.exists():
            # Try service account first
            try:
                creds = service_account.Credentials.from_service_account_file(
                    credentials_path, scopes=SCOPES)
                print("Authenticated using service account")
            except Exception:
                # Fall back to OAuth
                from google_auth_oauthlib.flow import InstalledAppFlow
                from google.auth.transport.requests import Request

                token_path = Path("token.json")
                if token_path.exists():
                    creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

                if not creds or not creds.valid:
                    if creds and creds.expired and creds.refresh_token:
                        from google.auth.transport.requests import Request
                        creds.refresh(Request())
                    else:
                        flow = InstalledAppFlow.from_client_secrets_file(
                            credentials_path, SCOPES)
                        creds = flow.run_local_server(port=0)

                    # Save credentials
                    with open(token_path, 'w') as token:
                        token.write(creds.to_json())
                print("Authenticated using OAuth")
        else:
            raise FileNotFoundError(
                f"Credentials file not found: {credentials_path}\n"
                "Please follow these steps:\n"
                "1. Go to https://console.cloud.google.com/\n"
                "2. Create a new project or select existing\n"
                "3. Enable Gmail API and Google Sheets API\n"
                "4. Create credentials (Service Account or OAuth 2.0)\n"
                "5. Download and save as 'credentials.json' in project root"
            )

        self.gmail_service = build('gmail', 'v1', credentials=creds)
        self.sheets_service = build('sheets', 'v4', credentials=creds)

    def search_venmo_payments(self, days_back: int = 7) -> List[Dict]:
        """Search Gmail for Venmo payment notifications"""
        try:
            # Calculate date range
            after_date = (datetime.now() - timedelta(days=days_back)).strftime('%Y/%m/%d')

            # Search for Venmo payment emails
            # Venmo sends emails from venmo@venmo.com with subject "You received..."
            query = f'from:venmo@venmo.com subject:"You received" after:{after_date}'

            results = self.gmail_service.users().messages().list(
                userId='me',
                q=query
            ).execute()

            messages = results.get('messages', [])
            print(f"Found {len(messages)} Venmo payment notification(s)")

            payment_data = []
            for message in messages:
                payment_info = self._parse_venmo_email(message['id'])
                if payment_info:
                    payment_data.append(payment_info)

            return payment_data

        except HttpError as error:
            print(f"Error searching Gmail: {error}")
            return []

    def _parse_venmo_email(self, message_id: str) -> Optional[Dict]:
        """Parse a Venmo payment email to extract payment details"""
        try:
            message = self.gmail_service.users().messages().get(
                userId='me',
                id=message_id,
                format='full'
            ).execute()

            # Get email body
            payload = message.get('payload', {})
            headers = payload.get('headers', [])

            # Extract subject and date
            subject = ''
            date = ''
            for header in headers:
                if header['name'] == 'Subject':
                    subject = header['value']
                elif header['name'] == 'Date':
                    date = header['value']

            # Get email body (handle both plain and HTML)
            body = self._get_email_body(payload)

            if not body:
                return None

            # Parse Venmo payment information
            # Pattern 1: "You received $XX.XX from @username"
            # Pattern 2: "You received $XX.XX from Username (@username)"
            amount_pattern = r'You received \$?([\d,]+\.\d{2})'
            from_pattern = r'from (@?[\w-]+)|from ([\w\s]+) \(@?([\w-]+)\)'

            amount_match = re.search(amount_pattern, body, re.IGNORECASE)
            from_match = re.search(from_pattern, body, re.IGNORECASE)

            if amount_match and from_match:
                amount_str = amount_match.group(1).replace(',', '')
                amount = float(amount_str)

                # Extract Venmo handle or name
                venmo_handle = None
                name = None

                if from_match.group(1):  # Just @username format
                    venmo_handle = from_match.group(1).replace('@', '').lower()
                elif from_match.group(2) and from_match.group(3):  # Name (@username) format
                    name = from_match.group(2).strip()
                    venmo_handle = from_match.group(3).replace('@', '').lower()

                return {
                    'message_id': message_id,
                    'amount': amount,
                    'venmo_handle': venmo_handle,
                    'name': name,
                    'subject': subject,
                    'date': date,
                    'body': body[:200]  # First 200 chars for debugging
                }

            return None

        except HttpError as error:
            print(f"Error parsing email {message_id}: {error}")
            return None

    def _get_email_body(self, payload: Dict) -> str:
        """Extract email body from payload"""
        body = ""

        if 'parts' in payload:
            for part in payload['parts']:
                if part['mimeType'] == 'text/plain':
                    if 'data' in part['body']:
                        body = base64.urlsafe_b64decode(part['body']['data']).decode('utf-8')
                        break
                elif part['mimeType'] == 'text/html' and not body:
                    if 'data' in part['body']:
                        html_body = base64.urlsafe_b64decode(part['body']['data']).decode('utf-8')
                        # Strip HTML tags for basic text extraction
                        body = re.sub(r'<[^>]+>', '', html_body)
        elif 'body' in payload and 'data' in payload['body']:
            body = base64.urlsafe_b64decode(payload['body']['data']).decode('utf-8')

        return body

    def get_sheet_data(self, tab_name: str) -> List[Dict]:
        """Get current data from Google Sheet tab"""
        try:
            range_name = f"{tab_name}!A:H"  # Name, Account, Equal portion, Recurring Extras, Extras, Credit, Total, Payment Status

            result = self.sheets_service.spreadsheets().values().get(
                spreadsheetId=self.sheet_id,
                range=range_name
            ).execute()

            values = result.get('values', [])

            if not values or len(values) < 2:
                print(f"No data found in tab '{tab_name}'")
                return []

            # Parse rows (skip header row)
            sheet_data = []
            for i, row in enumerate(values[1:], start=2):  # Start from row 2 (after header)
                if len(row) < 7 or row[0] == 'Total':  # Skip totals row
                    continue

                # Extract data
                name = row[0] if len(row) > 0 else ''
                account = row[1] if len(row) > 1 else ''
                total_str = row[6] if len(row) > 6 else '$0.00'
                payment_status = row[7] if len(row) > 7 else 'Pending'

                # Parse amount (remove $ and commas)
                total_amount = float(total_str.replace('$', '').replace(',', '')) if total_str else 0.0

                sheet_data.append({
                    'row_index': i,
                    'name': name,
                    'account': account,
                    'total': total_amount,
                    'payment_status': payment_status
                })

            return sheet_data

        except HttpError as error:
            print(f"Error reading sheet: {error}")
            return []

    def match_payment_to_person(self, payment: Dict, sheet_data: List[Dict]) -> Optional[Dict]:
        """Match a Venmo payment to a person in the sheet using amount and name"""
        amount = payment['amount']
        venmo_handle = payment['venmo_handle']

        if not venmo_handle:
            return None

        # Try to match
        for person in sheet_data:
            # Skip if already paid
            if person['payment_status'].lower() == 'paid':
                continue

            # Check amount match (allow small tolerance for floating point)
            amount_matches = abs(person['total'] - amount) < 0.01

            if not amount_matches:
                continue

            # Check name match: person's name must appear in venmo handle (case-insensitive)
            # Like SQL: WHERE venmo_handle LIKE '%name%'
            name_from_sheet = person['name'].lower()
            name_in_handle = name_from_sheet in venmo_handle

            if name_in_handle:
                print(f"  ✓ Match found: {person['name']} (${amount:.2f})")
                print(f"    Venmo handle: @{venmo_handle}")
                return person

        return None

    def update_payment_status(self, tab_name: str, row_index: int, status: str = "Paid"):
        """Update payment status in Google Sheet"""
        try:
            # Column H is the Payment Status column (8th column)
            range_name = f"{tab_name}!H{row_index}"

            body = {'values': [[status]]}

            self.sheets_service.spreadsheets().values().update(
                spreadsheetId=self.sheet_id,
                range=range_name,
                valueInputOption='USER_ENTERED',
                body=body
            ).execute()

            print(f"  ✓ Updated row {row_index} to '{status}'")
            return True

        except HttpError as error:
            print(f"  ✗ Error updating sheet: {error}")
            return False

    def process_payments(self, tab_name: str, days_back: int = 7):
        """Main processing loop: search for payments and update sheet"""
        print(f"\nSearching for Venmo payments (last {days_back} days)...")

        # Search Gmail for Venmo payments
        payments = self.search_venmo_payments(days_back)

        if not payments:
            print("No Venmo payment notifications found")
            return

        # Get current sheet data
        print(f"\nReading Google Sheet tab '{tab_name}'...")
        sheet_data = self.get_sheet_data(tab_name)

        if not sheet_data:
            print("No pending payments in sheet")
            return

        print(f"\nMatching {len(payments)} payment(s) to {len(sheet_data)} person(s)...")

        # Match and update
        matched_count = 0
        for payment in payments:
            print(f"\nProcessing payment: ${payment['amount']:.2f} from @{payment['venmo_handle']}")

            matched_person = self.match_payment_to_person(payment, sheet_data)

            if matched_person:
                success = self.update_payment_status(tab_name, matched_person['row_index'], "Paid")
                if success:
                    matched_count += 1
                    # Update local data to avoid duplicate matches
                    matched_person['payment_status'] = 'Paid'
            else:
                print(f"  ✗ No match found (amount or name mismatch)")

        print(f"\n{'='*60}")
        print(f"Summary: Updated {matched_count} payment(s)")
        print(f"{'='*60}")


def main():
    """Main entry point"""
    import argparse

    parser = argparse.ArgumentParser(description='Monitor Gmail for Venmo payments and update Google Sheet')
    parser.add_argument('tab_name', help='Google Sheet tab name (e.g., "Mar 26")')
    parser.add_argument('--days', type=int, default=7, help='Number of days to search back (default: 7)')
    parser.add_argument('--credentials', default='credentials.json', help='Path to credentials file')
    parser.add_argument('--watch', action='store_true', help='Run in watch mode (continuous monitoring)')
    parser.add_argument('--interval', type=int, default=300, help='Check interval in seconds for watch mode (default: 300)')

    args = parser.parse_args()

    try:
        monitor = VenmoPaymentMonitor()
        monitor.authenticate(args.credentials)

        if args.watch:
            print(f"Starting watch mode (checking every {args.interval} seconds)...")
            print("Press Ctrl+C to stop")

            while True:
                try:
                    monitor.process_payments(args.tab_name, args.days)
                    print(f"\nWaiting {args.interval} seconds before next check...")
                    time.sleep(args.interval)
                except KeyboardInterrupt:
                    print("\n\nStopping watch mode...")
                    break
        else:
            # One-time check
            monitor.process_payments(args.tab_name, args.days)

    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
