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
from fastapi.middleware.cors import CORSMiddleware

BASE_DIR = Path(__file__).parent

# route -> (script path relative to BASE_DIR, allowed HTTP methods)
ROUTES: dict[str, tuple[str, tuple[str, ...]]] = {
    "login": ("login/login.py", ("POST",)),
    "register": ("register/register.py", ("POST",)),
    "create_family": ("create_family/create_family.py", ("POST",)),
    "get_user_families": ("get_user_families/get_user_families.py", ("GET", "POST")),
    "get_family_members": ("get_family_members/get_family_members.py", ("GET", "POST")),
    "get_invitations": ("get_invitations/get_invitations.py", ("GET", "POST")),
    "send_invitation": ("send_invitation/send_invitation.py", ("POST",)),
    "respond_invitation": ("respond_invitation/respond_invitation.py", ("POST",)),
    "update_member_role": ("update_member_role/update_member_role.py", ("POST",)),
    "generate_guest_qr": ("generate_guest_qr/generate_guest_qr.py", ("POST",)),
    "device_pair": ("device_pair/device_pair.py", ("POST",)),
    "list_devices": ("list_devices/list_devices.py", ("GET", "POST")),
    "decommission_device": ("decommission_device/decommission_device.py", ("POST",)),
    "ota_update": ("ota_update/ota_update.py", ("POST",)),
    "maintenance_mode": ("maintenance_mode/maintenance_mode.py", ("GET", "POST")),
    "control_device": ("control_device/control_device.py", ("POST",)),
    "device_status_update": ("control_device/device_status_update.py", ("POST",)),
    "dashboard": ("dashboard/get_family_dashboard.py", ("POST",)),
    # 輪詢專用的輕量指令狀態查詢。App 原本拿 /dashboard 輪詢，而 dashboard
    # 每次呼叫都會寫一筆 DASHBOARD_VIEWED 進 audit_logs —— 按一次解鎖最多會
    # 在雜湊鏈裡塞進 15 筆假的「檢視儀表板」紀錄。這支不寫稽核日誌。
    "get_command_status": (
        "get_command_status/get_command_status.py",
        ("GET", "POST"),
    ),
    # UC1.5 目前場域情境。腳本在 f4b12df 就 commit 了，但跟 UC1.3 / UC1.4
    # 當初一樣沒掛進路由表，HTTP 打不到。
    "get_family_context": ("get_family_context/get_family_context.py", ("GET", "POST")),
    # UC1.3 閘道器初始化與屋主綁定。腳本早已寫好，但一直沒掛進路由表，
    # 導致 HTTP 打不到、App 無法實作對應畫面。
    # provision_gateway_identity.py 不在此列：它是 argparse CLI 佈建工具
    # （在 Gateway 本機產生 Identity），不是 CGI 端點。
    "gateway_initialize": ("gateway_initialization/gateway_initialize.py", ("POST",)),
    "gateway_initialization_status": (
        "gateway_initialization/get_gateway_initialization_status.py",
        ("GET", "POST"),
    ),
    # UC1.4 跨場域閘道器協作（信任綁定），同樣是寫好但沒掛路由。
    "create_gateway_trust": ("gateway_collaboration/create_gateway_trust.py", ("POST",)),
    "confirm_gateway_trust": (
        "gateway_collaboration/confirm_gateway_trust.py",
        ("POST",),
    ),
    "list_gateway_trusts": (
        "gateway_collaboration/list_gateway_trusts.py",
        ("GET", "POST"),
    ),
    "revoke_gateway_trust": ("gateway_collaboration/revoke_gateway_trust.py", ("POST",)),
}

app = FastAPI(title="Family/Device Management API")

# Flutter Web 版（`flutter run -d chrome`）是從另一個 origin 發請求，沒有 CORS
# 標頭瀏覽器會直接擋下來。部分 CGI 腳本（例如 list_devices.py）自己有印
# Access-Control-Allow-Origin，但 run_cgi() 只解析 Status 與 Content-Type 兩個
# 標頭，其餘一律丟棄，所以那些設定實際上沒有生效 —— 必須在閘道這層處理。
#
# allow_origins=["*"] 是配合「開發機 IP 會變、Web 版 port 也會變」的開發期設定。
# 這個 API 目前沒有 Cookie/Session（身分靠 payload 裡的 user_id），所以不涉及
# credentials 外洩；正式部署導入 Token 之後應改成明確的來源白名單。
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


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
