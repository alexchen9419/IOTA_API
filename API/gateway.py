"""
FastAPI CGI adapter for the family/device-management backend.

Every endpoint under API/ is a classic CGI script: it reads a JSON body from
stdin and prints a "Status: <code>" header followed by a JSON body. This
gateway runs each script as a subprocess per request, feeds it stdin/env the
way a real CGI server would, and translates its printed CGI response into a
proper HTTP response (status code included) so callers get correct 4xx/5xx
codes instead of always-200.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request, Response

BASE_DIR = Path(__file__).parent

# route -> (script path relative to BASE_DIR, allowed HTTP methods)
ROUTES: dict[str, tuple[str, tuple[str, ...]]] = {
    "login": ("login/login.py", ("POST",)),
    "register": ("register/register.py", ("POST",)),
    "send_invitation": ("send_invitation/send_invitation.py", ("POST",)),
    "respond_invitation": ("respond_invitation/respond_invitation.py", ("POST",)),
    "update_member_role": ("update_member_role/update_member_role.py", ("POST",)),
    "generate_guest_qr": ("generate_guest_qr/generate_guest_qr.py", ("POST",)),
    "device_pair": ("device_pair/device_pair.py", ("POST",)),
    "list_devices": ("list_devices/list_devices.py", ("GET", "POST")),
    "decommission_device": ("decommission_device/decommission_device.py", ("POST",)),
    "control_device": ("control_device/control_device.py", ("POST",)),
    "device_status_update": ("control_device/device_status_update.py", ("POST",)),
    "dashboard": ("dashboard/get_family_dashboard.py", ("POST",)),
}

app = FastAPI(title="Family/Device Management API")


def run_cgi(script_rel_path: str, method: str, query_string: str, body: bytes) -> Response:
    script_path = BASE_DIR / script_rel_path
    env = {
        **os.environ,
        "REQUEST_METHOD": method,
        "QUERY_STRING": query_string,
        "CONTENT_LENGTH": str(len(body)),
        "CONTENT_TYPE": "application/json",
    }

    proc = subprocess.run(
        [sys.executable, "-u", script_path.name],
        input=body,
        capture_output=True,
        env=env,
        cwd=script_path.parent,
        timeout=30,
    )

    stdout = proc.stdout
    if not stdout.strip():
        detail = proc.stderr.decode("utf-8", errors="replace") or f"exit code {proc.returncode}"
        return Response(content=f'{{"status":"Error","msg":"CGI script produced no output","detail":{detail!r}}}',
                         status_code=502, media_type="application/json")

    header_blob, _, response_body = stdout.partition(b"\n\n")
    if not response_body and b"\r\n\r\n" in stdout:
        header_blob, _, response_body = stdout.partition(b"\r\n\r\n")

    status_code = 200
    media_type = "application/json"
    for line in header_blob.decode("utf-8", errors="replace").splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip().lower()
        value = value.strip()
        if key == "status":
            status_code = int(value.split()[0])
        elif key == "content-type":
            media_type = value

    return Response(content=response_body, status_code=status_code, media_type=media_type)


async def dispatch(name: str, request: Request) -> Response:
    script_rel_path, allowed_methods = ROUTES[name]
    if request.method not in allowed_methods:
        return Response(content='{"status":"Error","msg":"Method not allowed"}',
                         status_code=405, media_type="application/json")
    body = await request.body()
    return run_cgi(script_rel_path, request.method, request.url.query, body)


for _name in ROUTES:
    def _make_handler(route_name: str):
        async def handler(request: Request):
            return await dispatch(route_name, request)
        return handler

    app.add_api_route(f"/{_name}", _make_handler(_name), methods=["GET", "POST"])


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}
