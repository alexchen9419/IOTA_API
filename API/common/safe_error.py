"""Keep exception details on the server; never hand them to API clients.

Usage inside an `except ... as e:` block of a CGI script:

    response_json({"status": "Error", "msg": "...", "detail": public_detail(e)}, 500)

The full exception and traceback go to stderr (the gateway logs it). The returned value is
None for clients, and the traceback only when the server is started with DEBUG=1.
"""
import os
import sys
import traceback


def public_detail(exc=None):
    print(f"[error] {type(exc).__name__}: {exc}", file=sys.stderr)
    tb = traceback.format_exc()
    if tb and not tb.startswith("NoneType"):
        print(tb, file=sys.stderr)
    return tb if os.getenv("DEBUG", "0") == "1" else None
