"""Smoke-test every route of the running API and look for leaked internals.

For each route in /openapi.json (except /guest, /healthz, /docs, /ledger/*) send three
bad requests: a non-object payload, a non-JSON body, and an empty payload. A route passes if
it is reachable, answers with valid JSON, and the body contains no internal details
(tracebacks, Python/MySQL error text, file paths).

Usage:  python tools/api_smoke_test.py [base_url]        (default http://localhost:8091)
Optional env: ANOMALY_SERVICE_TOKEN  (sent as X-Service-Token to the protected route)
Exit code 0 = all routes pass.
"""
import json
import os
import re
import sys
import urllib.error
import urllib.request

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8091").rstrip("/")
TOKEN = os.getenv("ANOMALY_SERVICE_TOKEN", "")
LEAK = re.compile(
    r"Traceback|File \"|object has no attribute|object is not|pymysql|MySQL|mysql\.connector|"
    r"Errno|SQL syntax|Access denied|Can't connect|OperationalError|IntegrityError|"
    r"KeyError|TypeError|ValueError|AttributeError|JSONDecodeError|\bline \d+|/app/|\.py\b",
    re.I,
)
SKIP_EXACT = {"/guest", "/healthz", "/docs", "/redoc", "/openapi.json"}
SKIP_PREFIX = ("/ledger",)


def call(method, path, body=None, headers=None):
    req = urllib.request.Request(BASE + path, data=body, method=method,
                                 headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


spec = json.loads(call("GET", "/openapi.json")[1])
routes = sorted(p for p in spec["paths"] if p not in SKIP_EXACT and not p.startswith(SKIP_PREFIX))
cases = {
    "list-payload": b'{"payload": []}',
    "not-json": b"this is not json",
    "empty-payload": b'{"payload": {}}',
}
headers = {"X-Service-Token": TOKEN} if TOKEN else {}
bad = 0
print(f"{len(routes)} routes against {BASE}\n")
for path in routes:
    problems = []
    codes = []
    for name, body in cases.items():
        status, text = call("POST", path, body, headers)
        codes.append(status)
        try:
            json.loads(text)
        except ValueError:
            problems.append(f"{name}: not JSON (HTTP {status})")
        if status in (404, 405) and status != 405:
            problems.append(f"{name}: HTTP {status}")
        m = LEAK.search(text)
        if m:
            problems.append(f"{name}: leaks '{m.group(0)}' in {text[:120]!r}")
    bad += bool(problems)
    print(("FAIL " if problems else "ok   ") + f"{path:34} {codes}")
    for p in problems:
        print("       - " + p)
print(f"\n{len(routes) - bad}/{len(routes)} routes clean")
sys.exit(1 if bad else 0)
