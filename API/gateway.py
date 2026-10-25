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

import hmac
import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

BASE_DIR = Path(__file__).parent
log = logging.getLogger("gateway")
# DEBUG=1 才會把例外細節（traceback）回給客戶端；預設一律只寫進伺服器日誌。
DEBUG = os.environ.get("DEBUG", "0") == "1"

# route -> (script path relative to BASE_DIR, allowed HTTP methods)
ROUTES: dict[str, tuple[str, tuple[str, ...]]] = {
    "login": ("login/login.py", ("POST",)),
    "register": ("register/register.py", ("POST",)),
    "change_password": ("change_password/change_password.py", ("POST",)),
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
    # UC4.2 訪客頁（GET /guest）用 token 查可開的門；開門本身走 control_device
    "guest_access": ("control_device/guest_access.py", ("POST",)),
    # UC3.5 撤銷訪客通行碼。revoke 早就寫好但沒掛路由；list 是新增的——
    # App 只在產生 QR 那次拿到 token_id，沒有清單就不知道要撤銷哪一個。
    "list_guest_tokens": ("control_device/list_guest_tokens.py", ("POST",)),
    "revoke_guest_token": ("control_device/revoke_guest_token.py", ("POST",)),
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
    # UC4.4 異常通報。這支腳本本身不驗證呼叫者（見 report_security_anomaly_README.md），
    # 所以在閘道這層要求服務金鑰（SERVICE_TOKEN_ROUTES），不然任何人都能灌假警報。
    "report_security_anomaly": ("security_anomaly/report_security_anomaly.py", ("POST",)),
}

# route -> 存放服務金鑰的環境變數；請求要帶 X-Service-Token（至少 32 字元）。
SERVICE_TOKEN_ROUTES = {"report_security_anomaly": "ANOMALY_SERVICE_TOKEN"}


def check_service_token(route: str, request: Request) -> Optional[Response]:
    """受保護的路由：金鑰沒設或不符就擋下，回 None 表示放行。"""
    env_name = SERVICE_TOKEN_ROUTES.get(route)
    if not env_name:
        return None
    expected = os.environ.get(env_name, "")
    if len(expected) < 32:
        return Response(content='{"status":"Error","msg":"Service token not configured"}',
                        status_code=503, media_type="application/json")
    given = request.headers.get("x-service-token", "")
    if not hmac.compare_digest(given.encode(), expected.encode()):
        return Response(content='{"status":"Error","msg":"Unauthorized"}',
                        status_code=401, media_type="application/json")
    return None


def scrub_error_body(body: bytes, status_code: int) -> bytes:
    """兜底：5xx 的 JSON 回應拿掉 detail / traceback，避免任何腳本漏網洩漏內部細節。"""
    if DEBUG or status_code < 500:
        return body
    try:
        data = json.loads(body)
    except ValueError:
        return body
    if isinstance(data, dict) and ("detail" in data or "traceback" in data):
        data.pop("detail", None)
        data.pop("traceback", None)
        return json.dumps(data, ensure_ascii=False).encode("utf-8")
    return body

app = FastAPI(title="Family/Device Management API")

from ledger_bridge.router import router as ledger_router
app.include_router(ledger_router)

# Flutter Web 版（`flutter run -d chrome`）是從另一個 origin 發請求，沒有 CORS
# 標頭瀏覽器會直接擋下來。部分 CGI 腳本（例如 list_devices.py）自己有印
# Access-Control-Allow-Origin，但 run_cgi() 只解析 Status 與 Content-Type 兩個
# 標頭，其餘一律丟棄，所以那些設定實際上沒有生效 —— 必須在閘道這層處理。
#
# 來源白名單：環境變數 CORS_ORIGINS（逗號分隔的完整 origin，例如
# https://app.example.com）。另外固定允許 localhost / 127.0.0.1 的任意 port，
# 因為 `flutter run -d chrome` 的 port 每次不同。其他網站的頁面不能從瀏覽器呼叫這個 API。
# 原生 App（Android/iOS）不受 CORS 管，不受影響。
_cors_origins = [o.strip() for o in os.environ.get("CORS_ORIGINS", "").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)


def run_cgi(script_rel_path: str, method: str, query_string: str, body: bytes,
            host: str = "", scheme: str = "http") -> Response:
    script_path = BASE_DIR / script_rel_path
    env = {
        **os.environ,
        "REQUEST_METHOD": method,
        "QUERY_STRING": query_string,
        "CONTENT_LENGTH": str(len(body)),
        "CONTENT_TYPE": "application/json",
        # generate_guest_qr.py 沒設 GUEST_QR_BASE_URL 時用這個組訪客頁網址
        "HTTP_HOST": host,
        "REQUEST_SCHEME": scheme,
    }

    proc = subprocess.run(
        [sys.executable, "-u", script_path.name],
        input=body,
        capture_output=True,
        env=env,
        cwd=script_path.parent,
        timeout=30,
    )

    if proc.stderr:
        log.error("CGI %s stderr:\n%s", script_rel_path, proc.stderr.decode("utf-8", errors="replace"))

    stdout = proc.stdout
    if not stdout.strip():
        log.error("CGI %s produced no output (exit code %s)", script_rel_path, proc.returncode)
        return Response(content='{"status":"Error","msg":"CGI script produced no output"}',
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

    return Response(content=scrub_error_body(response_body, status_code),
                    status_code=status_code, media_type=media_type)


async def dispatch(name: str, request: Request) -> Response:
    script_rel_path, allowed_methods = ROUTES[name]
    if request.method not in allowed_methods:
        return Response(content='{"status":"Error","msg":"Method not allowed"}',
                         status_code=405, media_type="application/json")
    denied = check_service_token(name, request)
    if denied is not None:
        return denied
    body = await request.body()
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    return run_cgi(script_rel_path, request.method, request.url.query, body,
                   host=request.headers.get("host", ""), scheme=scheme)


for _name in ROUTES:
    def _make_handler(route_name: str):
        async def handler(request: Request):
            return await dispatch(route_name, request)
        return handler

    app.add_api_route(f"/{_name}", _make_handler(_name), methods=["GET", "POST"])


GUEST_PAGE = BASE_DIR / "guest_page" / "guest.html"


@app.get("/guest")
async def guest_page():
    """訪客掃 QR 打開的頁面。token 在 URL fragment，不會出現在這個請求裡。"""
    return Response(
        content=GUEST_PAGE.read_bytes(),
        media_type="text/html; charset=utf-8",
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
    )


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}
