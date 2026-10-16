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
TAB_RE = re.compile(r"^[A-Z][a-z]{2} \d{2}$")


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
    """One authenticated Sheets client, made on first use."""
    global _sheets
    with _sheets_lock:
        if _sheets is None:
            from update_google_sheet import GoogleSheetsUpdater
            u = GoogleSheetsUpdater()
            u.authenticate("credentials.json")
            _sheets = u
        return _sheets


def _amount(s) -> float:
    try:
        return float(str(s).replace("$", "").replace(",", "") or 0)
    except ValueError:
        return 0.0


def list_months():
    u = sheets()
    meta = u.service.spreadsheets().get(
        spreadsheetId=u.sheet_id, fields="sheets.properties.title").execute()
    tabs = [s["properties"]["title"] for s in meta.get("sheets", [])]
    tabs = [t for t in tabs if TAB_RE.match(t)]
    tabs.sort(key=lambda t: datetime.strptime(t, "%b %y"), reverse=True)
    return tabs


def month_status(tab: str):
    u = sheets()
    rows = u.service.spreadsheets().values().get(
        spreadsheetId=u.sheet_id, range=f"'{tab}'!A1:I40").execute().get("values", [])
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
    st = {}
    if STATE_FILE.exists():
        st = json.loads(STATE_FILE.read_text()).get(tab, {})
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


def start_pipeline(args, tab=None) -> bool:
    """Run run_pipeline.py in the background, output appended to the pipeline log."""
    if _run["running"]:
        return False

    def go():
        _run.update(running=True, started=datetime.now().isoformat(timespec="seconds"), tab=tab)
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
        if not secrets.compare_digest(tok, TOKEN):
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
        if not self._auth():
            return
        try:
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
        if not self._auth():
            return
        try:
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
                mon, year = bill_month_from_pdf(tmp)
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


if __name__ == "__main__":
    print(f"[api] T-Mobile Bills API on 0.0.0.0:{PORT}  (token: {TOKEN_FILE})")
    sys.stdout.flush()
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
