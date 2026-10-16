#!/usr/bin/env python3
"""Small HTTP API for the T-Mobile Bills iPhone app. Standard library only.

Runs on the Mac next to the pipeline. The phone shows who has paid, uploads a
bill PDF, and starts the pipeline. The pipeline, WhatsApp and the sheet stay on
the Mac. Every request except /api/ping needs the shared token from
state/api_token.txt in the X-Token header.

  GET  /api/ping                 liveness, no token
  GET  /api/months               built month tabs, newest first, + run state
  GET  /api/status?tab=Sep%2026  people, paid/pending sums, owed by payer
  GET  /api/run                  pipeline run state + last run's log lines
  POST /api/bill                 raw PDF body -> bills/SummaryBill<Mon><YYYY>.pdf, starts pipeline
  POST /api/remind?tab=Sep%2026  DM everyone still unpaid now (--force)
  POST /api/refresh              one pipeline pass (payment matching)
"""
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT / "src"))

TOKEN_FILE = ROOT / "state" / "api_token.txt"
STATE_FILE = ROOT / "state" / "pipeline_state.json"
LOG_FILE = ROOT / "logs" / "pipeline.log"
PORT = int(os.environ.get("TMOBILE_API_PORT", "8787"))
# Name the months. A stray tab like "Old 25" must not reach strptime.
TAB_RE = re.compile(r"^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) \d{2}$")


def load_token() -> str:
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not TOKEN_FILE.exists():
        TOKEN_FILE.write_text(secrets.token_urlsafe(24))
        os.chmod(TOKEN_FILE, 0o600)
    return TOKEN_FILE.read_text().strip()


TOKEN = load_token()

_sheets = None
_sheets_lock = threading.Lock()


def sheets():
    """One authenticated Sheets client, made on first use.

    Never run the browser consent flow here. A request thread under launchd
    has no browser, and the flow would block forever with the lock held. When
    the token needs re-consent, raise so the request gets a JSON 500."""
    global _sheets
    with _sheets_lock:
        if _sheets is None:
            from google_auth import sheets_drive_credentials
            from googleapiclient.discovery import build
            from update_google_sheet import GoogleSheetsUpdater
            u = GoogleSheetsUpdater()
            try:
                creds = sheets_drive_credentials(interactive=False)
            except RuntimeError as err:
                raise RuntimeError(
                    f"Google token needs re-consent; run the pipeline once on the Mac ({err})")
            u.creds = creds
            u.service = build("sheets", "v4", credentials=creds)
            _sheets = u
        return _sheets


def _http(u):
    """A fresh transport for one request. httplib2.Http is not thread-safe,
    and the ThreadingHTTPServer runs requests in parallel, so no two threads
    may share a socket. The timeout keeps a dead socket from pinning a thread."""
    import httplib2
    from google_auth_httplib2 import AuthorizedHttp
    return AuthorizedHttp(u.creds, http=httplib2.Http(timeout=30))


def _amount(s) -> float:
    try:
        return float(str(s).replace("$", "").replace(",", "") or 0)
    except ValueError:
        return 0.0


def list_months():
    u = sheets()
    meta = u.service.spreadsheets().get(
        spreadsheetId=u.sheet_id, fields="sheets.properties.title").execute(http=_http(u))
    tabs = [s["properties"]["title"] for s in meta.get("sheets", [])]
    tabs = [t for t in tabs if TAB_RE.match(t)]
    tabs.sort(key=lambda t: datetime.strptime(t, "%b %y"), reverse=True)
    return tabs


def month_status(tab: str):
    u = sheets()
    rows = u.service.spreadsheets().values().get(
        spreadsheetId=u.sheet_id, range=f"'{tab}'!A1:I40").execute(http=_http(u)).get("values", [])
    pays_for = u.config.get("pays_for", {})
    payer_of = {d: p for p, ds in pays_for.items() for d in ds}
    people = []
    for r in rows[1:]:
        r = r + [""] * (9 - len(r))
        if not r[0] or r[0] == "Total":
            continue
        people.append({
            "name": r[0],
            "total": round(_amount(r[6]), 2),
            "status": "paid" if r[7].strip().lower() == "paid" else "pending",
            "note": r[8],
            "payer": payer_of.get(r[0], r[0]),
        })
    paid = [p for p in people if p["status"] == "paid"]
    pending = [p for p in people if p["status"] == "pending"]
    owed = {}
    for p in pending:
        owed[p["payer"]] = round(owed.get(p["payer"], 0.0) + p["total"], 2)
    # run_pipeline.py rewrites this file in place. A read that lands between
    # the truncate and the write sees an empty file. Fall back to no flags.
    st = {}
    if STATE_FILE.exists():
        try:
            st = json.loads(STATE_FILE.read_text()).get(tab, {}) or {}
        except (json.JSONDecodeError, OSError, AttributeError):
            st = {}
    return {
        "tab": tab,
        "people": people,
        "paid_count": len(paid),
        "paid_sum": round(sum(p["total"] for p in paid), 2),
        "pending_count": len(pending),
        "pending_sum": round(sum(p["total"] for p in pending), 2),
        "owed_by_payer": [{"payer": k, "amount": v}
                          for k, v in sorted(owed.items(), key=lambda x: -x[1])],
        "bill_total": round(sum(p["total"] for p in people), 2),
        "announced": bool(st.get("announced")),
        "announced_at": st.get("announced_at"),
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
    }


_run = {"running": False, "started": None, "tab": None}
_run_lock = threading.Lock()


def start_pipeline(args, tab=None) -> bool:
    """Run run_pipeline.py in the background, output appended to the pipeline log."""
    # Claim the run under the lock before the thread starts, so two POSTs that
    # land together cannot both spawn run_pipeline.py.
    with _run_lock:
        if _run["running"]:
            return False
        _run.update(running=True, started=datetime.now().isoformat(timespec="seconds"), tab=tab)

    def go():
        try:
            with open(LOG_FILE, "a") as lf:
                subprocess.run([sys.executable, "src/run_pipeline.py", *args],
                               stdout=lf, stderr=subprocess.STDOUT)
        finally:
            _run["running"] = False

    threading.Thread(target=go, daemon=True).start()
    return True


def last_run_lines(n: int = 40):
    if not LOG_FILE.exists():
        return []
    lines = LOG_FILE.read_text(errors="ignore").splitlines()
    start = max((i for i, l in enumerate(lines) if l.startswith("Pipeline run 20")), default=0)
    keep = [l for l in lines[start:]
            if l.startswith(("[", "✓", "✗", "Pipeline run", "Summary"))]
    return keep[-n:]


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, obj) -> None:
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _auth(self) -> bool:
        tok = self.headers.get("X-Token") or \
            self.headers.get("Authorization", "").replace("Bearer ", "")
        # Compare bytes. compare_digest raises TypeError on a non-ASCII str,
        # and http.server decodes header bytes as Latin-1, so a pasted smart
        # quote or non-breaking space in the token must still answer 401.
        tok_b = tok.encode("latin-1", "replace")
        if not secrets.compare_digest(tok_b, TOKEN.encode()):
            self._send(401, {"error": "bad token"})
            return False
        return True

    def log_message(self, fmt, *args):
        sys.stdout.write("[api] " + (fmt % args) + "\n")
        sys.stdout.flush()

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path == "/api/ping":
            return self._send(200, {"ok": True, "host": os.uname().nodename})
        try:
            if not self._auth():
                return
            if u.path == "/api/months":
                return self._send(200, {"months": list_months(), "run": _run})
            if u.path == "/api/status":
                tab = q.get("tab", [None])[0] or (list_months() or [None])[0]
                if not tab:
                    return self._send(404, {"error": "no months yet"})
                return self._send(200, month_status(tab))
            if u.path == "/api/run":
                return self._send(200, {**_run, "log": last_run_lines()})
            return self._send(404, {"error": "not found"})
        except Exception as e:  # report, never crash the server
            return self._send(500, {"error": f"{type(e).__name__}: {e}"})

    def do_POST(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        try:
            if not self._auth():
                return
            if u.path == "/api/bill":
                n = int(self.headers.get("Content-Length", "0"))
                if n <= 0 or n > 30_000_000:
                    return self._send(400, {"error": "missing or oversized body"})
                data = self.rfile.read(n)
                if not data.startswith(b"%PDF"):
                    return self._send(400, {"error": "not a PDF"})
                with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tf:
                    tf.write(data)
                    tmp = tf.name
                from fetch_tmobile_bill import bill_month_from_pdf
                try:
                    mon, year = bill_month_from_pdf(tmp)
                except Exception as err:
                    os.unlink(tmp)  # do not leave the temp copy behind
                    return self._send(400, {"error": f"Not a T-Mobile summary bill: {err}"})
                tab = f"{mon} {year[2:]}"
                dest = ROOT / "bills" / f"SummaryBill{mon}{year}.pdf"
                if dest.exists():
                    os.unlink(tmp)
                    return self._send(409, {"error": f"{dest.name} is already on the Mac", "tab": tab})
                os.replace(tmp, dest)
                started = start_pipeline(["--skip-fetch"], tab=tab)
                return self._send(200, {"saved": dest.name, "tab": tab, "pipeline_started": started})
            if u.path == "/api/remind":
                tab = q.get("tab", [None])[0]
                if not tab:
                    return self._send(400, {"error": "tab required"})
                started = start_pipeline(["--remind-tab", tab, "--force"], tab=tab)
                return self._send(200, {"pipeline_started": started, "tab": tab})
            if u.path == "/api/refresh":
                started = start_pipeline(["--skip-fetch"])
                return self._send(200, {"pipeline_started": started})
            return self._send(404, {"error": "not found"})
        except Exception as e:
            return self._send(500, {"error": f"{type(e).__name__}: {e}"})


class Server(ThreadingHTTPServer):
    # The default backlog of 5 refuses connections when the phone's poll,
    # pull-to-refresh and status calls land together.
    request_queue_size = 32


if __name__ == "__main__":
    print(f"[api] T-Mobile Bills API on 0.0.0.0:{PORT}  (token: {TOKEN_FILE})")
    sys.stdout.flush()
    Server(("0.0.0.0", PORT), Handler).serve_forever()
