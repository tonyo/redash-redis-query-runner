"""Redash API helpers for the demo: login, the authenticated `api()` call, job polling.

Stdlib only. Reads ADMIN_PASSWORD from ./.env (never printed).
Env overrides: REDASH_URL (default http://127.0.0.1:5000), ADMIN_EMAIL.
"""

import http.cookiejar
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

BASE = os.environ.get("REDASH_URL", "http://127.0.0.1:5000").rstrip("/")
EMAIL = os.environ.get("ADMIN_EMAIL", "admin@example.com")
ROOT = Path(__file__).resolve().parent.parent


def fail(msg):
    print(f"FAIL: {msg}", file=sys.stderr)
    sys.exit(1)


def check(cond, msg):
    if not cond:
        fail(msg)
    print(f"  ok: {msg}")


def read_env_password():
    path = ROOT / ".env"
    if not path.exists():
        fail(".env not found (run from the repo; it holds ADMIN_PASSWORD)")
    for line in path.read_text().splitlines():
        m = re.match(r"\s*ADMIN_PASSWORD\s*=\s*(.*)$", line)
        if m:
            return m.group(1).strip().strip("'\"")
    fail("ADMIN_PASSWORD not set in .env")


class _CsrfParser(HTMLParser):
    token = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "input" and a.get("name") == "csrf_token":
            self.token = a.get("value")


def login():
    """Session login via the /login form (CSRF token + cookies); stores the API key for api()."""
    global _key
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    try:
        html = opener.open(f"{BASE}/login", timeout=30).read().decode()
    except Exception as e:
        fail(f"cannot reach Redash at {BASE}: {e}")
    p = _CsrfParser()
    p.feed(html)
    check(p.token, "login form carries a CSRF token")
    form = urllib.parse.urlencode(
        {"csrf_token": p.token, "email": EMAIL, "password": read_env_password()}
    ).encode()
    req = urllib.request.Request(f"{BASE}/login", data=form)
    try:
        opener.open(req, timeout=30)
    except urllib.error.HTTPError as e:
        fail(f"login failed: HTTP {e.code}")
    csrf = next((c.value for c in jar if c.name == "csrf_token"), "")

    def get(path):
        req = urllib.request.Request(f"{BASE}{path}", headers={"X-CSRFToken": csrf})
        try:
            return json.load(opener.open(req, timeout=30))
        except urllib.error.HTTPError as e:
            fail(f"GET {path} with the login session: HTTP {e.code}")

    me = get("/api/session").get("user") or {}
    check(me.get("id"), "session is authenticated after login")
    me = get(f"/api/users/{me['id']}")  # own profile includes the API key
    key = me.get("api_key")
    check(key, f"logged in as {me.get('email')} and obtained an API key")
    _key = key


_key = None


def api(method, path, body=None, ok_errors=()):
    """Call the API with `Authorization: Key ...` (no cookies, so no CSRF check applies)."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=data,
        method=method,
        headers={"Authorization": f"Key {_key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            raw = r.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        if e.code in ok_errors:
            return json.loads(e.read() or b"null")
        fail(f"{method} {path}: HTTP {e.code} {e.read()[:300]!r}")


def wait_job(job):
    deadline = time.time() + 90
    while time.time() < deadline:
        j = api("GET", f"/api/jobs/{job['id']}")["job"]
        if j["status"] in (3, 4, 5):  # success, failure, canceled
            return j
        time.sleep(0.5)
    fail("job timed out")


def run_result(post_path, body):
    r = api("POST", post_path, body)
    if "query_result" in r:
        return r["query_result"]["data"], None
    j = wait_job(r["job"])
    if j["status"] != 3:
        return None, j.get("error") or "job failed"
    return api("GET", f"/api/query_results/{j['result']}")["query_result"]["data"], None
