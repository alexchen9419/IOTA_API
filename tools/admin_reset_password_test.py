"""admin_reset_password 權限檢查的離線測試：用假的 pymysql 連線，不需要 MySQL。

執行（repo 根目錄）：python -I tools/admin_reset_password_test.py
"""
import io, json, os, sys, types, importlib.util, contextlib
from pathlib import Path

API = str(Path(__file__).resolve().parents[1] / "API")
sys.path.insert(0, API)

# 假的 user_families / users 資料
FAM = {(12, "admin1"): "Admin", (12, "member1"): "Member", (12, "gone"): "Revoked",
       (12, "otheradmin"): "Member", (99, "otheradmin"): "Admin", (12, "disabled"): "Member"}
USERS = {"admin1": "Active", "member1": "Active", "otheradmin": "Active", "disabled": "Disabled"}
STATE = {"has_col": True, "updated": None, "audit": None}


class Cur:
    def __enter__(self): return self
    def __exit__(self, *a): pass
    def execute(self, sql, params=()):
        s = " ".join(sql.split())
        if s.startswith("SHOW COLUMNS FROM users"):
            self.r = [{"Field": "must_change_password"}] if STATE["has_col"] else []
        elif s.startswith("SELECT role FROM user_families WHERE family_id"):
            role = FAM.get((params[0], params[1])); self.r = [{"role": role}] if role else []
        elif s.startswith("SELECT COUNT(*) AS n FROM user_families"):
            self.r = [{"n": sum(1 for (f, u), r in FAM.items() if u == params[0] and r == "Admin")}]
        elif s.startswith("SELECT user_id, status FROM users"):
            st = USERS.get(params[0]); self.r = [{"user_id": params[0], "status": st}] if st else []
        elif s.startswith("UPDATE users SET password_hash"):
            STATE["updated"] = params[1]; self.r = []
        else:
            raise AssertionError("unexpected SQL: " + s)
    def fetchone(self): return self.r[0] if self.r else None


class Conn:
    def cursor(self): return Cur()
    def commit(self): pass
    def rollback(self): pass
    def close(self): pass


fake = types.ModuleType("pymysql")
fake.connect = lambda **k: Conn()
fake.MySQLError = type("MySQLError", (Exception,), {})
fake.cursors = types.SimpleNamespace(DictCursor=object)
sys.modules["pymysql"] = fake
dotenv = types.ModuleType("dotenv"); dotenv.load_dotenv = lambda *a, **k: None
sys.modules["dotenv"] = dotenv

spec = importlib.util.spec_from_file_location("arp", os.path.join(API, "admin_reset_password", "admin_reset_password.py"))
arp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(arp)
arp.append_audit_log = lambda cursor, **kw: STATE.__setitem__("audit", kw) or {}
arp.bcrypt = types.SimpleNamespace(hashpw=lambda p, s: b"hash", gensalt=lambda: b"salt")


def call(payload):
    STATE["updated"] = STATE["audit"] = None
    out = io.StringIO()
    sys.stdin = io.StringIO(json.dumps({"payload": payload}))
    with contextlib.redirect_stdout(out):
        try: arp.main()
        except SystemExit: pass
    text = out.getvalue()
    status = int(text.split("\n")[0].split()[1])
    return status, json.loads(text.split("\n\n", 1)[1])


cases = [
    ("成功重設一般成員", {"family_id": 12, "admin_uid": "admin1", "target_uid": "member1"}, 200),
    ("非 Admin 不能重設", {"family_id": 12, "admin_uid": "member1", "target_uid": "admin1"}, 403),
    ("不能重設自己", {"family_id": 12, "admin_uid": "admin1", "target_uid": "admin1"}, 400),
    ("已撤銷成員回 404", {"family_id": 12, "admin_uid": "admin1", "target_uid": "gone"}, 404),
    ("不在場域回 404", {"family_id": 12, "admin_uid": "admin1", "target_uid": "stranger"}, 404),
    ("在別的場域是 Admin 就拒絕", {"family_id": 12, "admin_uid": "admin1", "target_uid": "otheradmin"}, 403),
    ("停用帳號拒絕", {"family_id": 12, "admin_uid": "admin1", "target_uid": "disabled"}, 403),
    ("family_id 格式錯誤", {"family_id": "x", "admin_uid": "admin1", "target_uid": "member1"}, 400),
]
fails = 0
for name, payload, want in cases:
    status, body = call(payload)
    ok = status == want
    if want == 200:
        tp = body["data"]["temp_password"]
        ok = ok and len(tp) == 10 and not set(tp) & set("0O1lI") and STATE["updated"] == "member1" \
            and STATE["audit"]["action"] == "PASSWORD_RESET_BY_ADMIN" and "temp" not in json.dumps(STATE["audit"])
    else:
        ok = ok and STATE["updated"] is None and STATE["audit"] is None
    fails += not ok
    print(("PASS" if ok else "FAIL"), name, status, body.get("msg"))

STATE["has_col"] = False
status, body = call({"family_id": 12, "admin_uid": "admin1", "target_uid": "member1"})
ok = status == 500 and STATE["updated"] is None
fails += not ok
print(("PASS" if ok else "FAIL"), "缺欄位時拒絕執行", status)
print(f"{len(cases) + 1 - fails}/{len(cases) + 1} passed")
sys.exit(1 if fails else 0)
