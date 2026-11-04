"""Unit test for API/common/audit_log_service.py (audit_logs hash chain). No MySQL needed.

Checks:
  1. A default append still hashes exactly like the pre-unification shared module
     (callers already on the module keep the same hash format).
  2. Appends link prev_hash -> previous current_hash, starting from the 64-zero genesis.
  3. The chain head is read with a locking read (FOR UPDATE) after taking the
     audit_chain_lock row lock; without that table it falls back to FOR UPDATE only.
  4. Rows written by the old per-file formulas and new rows validate together
     with verify_chain_links() (only linkage is checked, no formula re-computation).
  5. Every endpoint wrapper that used to compute the chain itself now writes through
     the shared module (its row re-hashes with the shared formula and links correctly).

Run from the repo root (IOTA_API/):
  python -I tools/audit_chain_test.py
Or inside the api container:
  Get-Content tools\\audit_chain_test.py -Raw | docker exec -i iot_api python -
"""
import hashlib
import importlib
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent if "__file__" in globals() else Path("/app/tools")
API_ROOT = (HERE.parent / "API") if (HERE.parent / "API").is_dir() else Path("/app")
sys.path.insert(0, str(API_ROOT))

import common.audit_log_service as svc  # noqa: E402

AUDIT_COLUMNS = {  # mirrors API/schema.sql
    "id": "int", "command_id": "varchar(191)", "user_id": "varchar(191)", "u_id": "int",
    "actor_id": "varchar(191)", "actor_type": "varchar(32)", "device_id": "varchar(191)",
    "family_id": "int", "action": "varchar(64)", "parameters": "json", "raw_data": "json",
    "status": "varchar(32)", "decision": "varchar(32)", "reason": "varchar(255)",
    "prev_hash": "char(64)", "current_hash": "char(64)", "hash": "char(64)",
    "timestamp": "bigint", "created_at": "timestamp",
}


class FakeDB:
    def __init__(self, with_lock_table=True):
        self.rows = []
        self.tables = {"audit_logs", "users"} | ({"audit_chain_lock"} if with_lock_table else set())
        self.log = []
        self.commits = 0

    def cursor(self, *a, **k):
        return FakeCursor(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        pass

    def close(self):
        pass


class FakeCursor:
    def __init__(self, db):
        self.db = db
        self._result = []
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def close(self):
        pass

    def execute(self, sql, params=()):
        q = " ".join(sql.split())
        self.db.log.append(q)
        if q.startswith("SHOW TABLES LIKE"):
            name = params[0]
            self._result = [{"Tables_in_db": name}] if name in self.db.tables else []
        elif q.startswith("SHOW COLUMNS FROM `audit_logs`"):
            self._result = [{"Field": k, "Type": v} for k, v in AUDIT_COLUMNS.items()]
        elif "FROM `audit_chain_lock`" in q:
            self._result = [{"id": 1}]
        elif q.startswith("INSERT IGNORE INTO `audit_chain_lock`"):
            self._result = []
        elif q.startswith("SELECT `current_hash` AS h FROM audit_logs"):
            rows = [r for r in self.db.rows if r.get("current_hash")]
            self._result = [{"h": rows[-1]["current_hash"]}] if rows else []
        elif q.startswith("SELECT id FROM users WHERE user_id"):
            self._result = [{"id": 7}]
        elif q.startswith("INSERT INTO audit_logs"):
            cols = re.findall(r"`(\w+)`", q.split("VALUES")[0])
            row = dict(zip(cols, params))
            row["id"] = len(self.db.rows) + 1
            self.db.rows.append(row)
            self._result = []
        else:
            raise AssertionError(f"unexpected SQL in fake cursor: {q}")
        self.rowcount = len(self._result)

    def fetchone(self):
        return self._result[0] if self._result else None

    def fetchall(self):
        return list(self._result)


def _j(v):
    return json.dumps(v, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def pre_unification_shared_hash(row):
    """Verbatim hash_material of audit_log_service.py before this change."""
    material = {
        "command_id": row["command_id"], "actor_id": row["actor_id"], "actor_type": row["actor_type"],
        "family_id": row["family_id"], "device_id": row["device_id"], "action": row["action"],
        "parameters": json.loads(row["parameters"]), "status": row["status"],
        "decision": row["decision"], "reason": row["reason"], "timestamp": str(row["timestamp"]),
        "prev_hash": row["prev_hash"],
    }
    return hashlib.sha256(_j(material).encode("utf-8")).hexdigest()


def recompute_from_row(row, hash_raw_data):
    material = svc.build_hash_material(
        command_id=row["command_id"], actor_id=row["actor_id"], actor_type=row["actor_type"],
        family_id=row["family_id"], device_id=row["device_id"], action=row["action"],
        parameters=json.loads(row["parameters"]), status=row["status"], decision=row["decision"],
        reason=row["reason"], timestamp=row["timestamp"], prev_hash=row["prev_hash"],
        u_id=row.get("u_id"), raw_data=json.loads(row["raw_data"]), hash_raw_data=hash_raw_data,
    )
    return svc.compute_current_hash(material)


results = []


def check(label, ok, extra=""):
    results.append(bool(ok))
    print(f"[{'PASS' if ok else 'FAIL'}] {label}{(' - ' + extra) if extra and not ok else ''}")


# 1 + 2 + 3: default appends ------------------------------------------------------
db = FakeDB()
cur = db.cursor()
outs = [
    svc.append_audit_log(cur, actor_id="admin001", actor_type="USER", family_id=12, device_id=None,
                         action="SAMPLE_ACTION", parameters={"k": "v", "n": 1}, reason="R"),
    svc.append_audit_log(cur, actor_id="admin001", actor_type="USER", family_id=12, device_id="ESP32_1",
                         action="SAMPLE_ACTION", parameters={"中文": "值"}, status="Denied", decision="DENY"),
    svc.append_audit_log(cur, actor_id="admin002", actor_type="USER", family_id=None, device_id=None,
                         action="SAMPLE_ACTION", parameters={}),
]
check("default append hash == pre-unification shared formula (sample rows)",
      all(pre_unification_shared_hash(r) == r["current_hash"] for r in db.rows))
check("first row links to 64-zero genesis", db.rows[0]["prev_hash"] == "0" * 64)
check("each prev_hash == previous current_hash",
      all(db.rows[i]["prev_hash"] == db.rows[i - 1]["current_hash"] for i in range(1, len(db.rows))))
check("returned dict matches inserted row",
      all(o["current_hash"] == r["current_hash"] and o["prev_hash"] == r["prev_hash"] for o, r in zip(outs, db.rows)))
check("hash and current_hash columns identical", all(r["hash"] == r["current_hash"] for r in db.rows))
check("verify_chain_links() reports no break", svc.verify_chain_links(db.rows) == [])
lock_idx = [i for i, q in enumerate(db.log) if "FROM `audit_chain_lock`" in q and q.endswith("FOR UPDATE")]
head_idx = [i for i, q in enumerate(db.log) if q.startswith("SELECT `current_hash` AS h FROM audit_logs")]
check("chain head read uses FOR UPDATE", head_idx and all(db.log[i].endswith("FOR UPDATE") for i in head_idx))
check("audit_chain_lock row locked before every head read",
      len(lock_idx) == len(head_idx) == 3 and all(l < h for l, h in zip(lock_idx, head_idx)))

tampered = [dict(r) for r in db.rows]
tampered[1]["current_hash"] = "f" * 64
check("verify_chain_links() detects a broken link", len(svc.verify_chain_links(tampered)) == 1)

db2 = FakeDB(with_lock_table=False)
svc.append_audit_log(db2.cursor(), actor_id="a", actor_type="USER", family_id=1, device_id=None,
                     action="X", parameters={})
check("without audit_chain_lock table: no lock-table SQL, head still FOR UPDATE",
      not any("FROM `audit_chain_lock`" in q for q in db2.log)
      and any(q.startswith("SELECT `current_hash`") and q.endswith("FOR UPDATE") for q in db2.log))

# Optional extensions
db3 = FakeDB()
c3 = db3.cursor()
o = svc.append_audit_log(c3, actor_id="u1", actor_type="USER", family_id=1, device_id="d", action="A",
                         parameters={"p": 1}, raw_data={"evidence": "x"}, hash_raw_data=True,
                         command_id="CMD_1", user_id=None, u_id=5)
r = db3.rows[0]
check("command_id / user_id / u_id overrides are written",
      r["command_id"] == "CMD_1" and r["user_id"] is None and r["actor_id"] == "u1" and r["u_id"] == 5)
check("hash_raw_data=True covers raw_data (re-computable from the row)",
      recompute_from_row(r, True) == r["current_hash"] and recompute_from_row(r, False) != r["current_hash"])

# 4: mixed legacy + new rows ------------------------------------------------------
mixed = FakeDB()
mc = mixed.cursor()
legacy_prev = "0" * 64
for i in range(2):  # old maintenance_mode.py formula
    hp = {"command_id": f"MTN_{i}", "actor_id": "admin", "device_id": "d", "family_id": 1,
          "action": "MAINTENANCE_MODE_ENABLED", "parameters": {}, "status": "Active",
          "timestamp": 1700000000 + i, "prev_hash": legacy_prev}
    h = hashlib.sha256(json.dumps(hp, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    mixed.rows.append({"id": len(mixed.rows) + 1, "command_id": f"MTN_{i}", "prev_hash": legacy_prev,
                       "current_hash": h, "hash": h})
    legacy_prev = h
base = f"{legacy_prev}|2026-01-01T00:00:00|CMD|1|d|u|USER|CONTROL_DEVICE|OK|ALLOW|r|{{}}"  # old control_device.py formula
h = hashlib.sha256(base.encode("utf-8")).hexdigest()
mixed.rows.append({"id": 3, "command_id": "CMD", "prev_hash": legacy_prev, "current_hash": h, "hash": h})
svc.append_audit_log(mc, actor_id="u", actor_type="USER", family_id=1, device_id="d", action="NEW", parameters={})
svc.append_audit_log(mc, actor_id="u", actor_type="USER", family_id=1, device_id="d", action="NEW", parameters={})
check("new rows link onto the last legacy row", mixed.rows[3]["prev_hash"] == mixed.rows[2]["current_hash"])
check("mixed legacy/new chain validates with verify_chain_links()", svc.verify_chain_links(mixed.rows) == [])

# 5: endpoint wrappers ------------------------------------------------------------
os.environ.setdefault("DB_PASS", "unused")


def load(module_dir, name):
    path = str(API_ROOT / module_dir)
    if path not in sys.path:
        sys.path.insert(0, path)
    return importlib.import_module(name)


def wrapper_case(label, fn, hash_raw_data=False, expect_command_prefix=None):
    wdb = FakeDB()
    wdb.rows.append({"id": 1, "command_id": "seed", "prev_hash": "0" * 64, "current_hash": "a" * 64, "hash": "a" * 64})
    try:
        fn(wdb)
    except Exception as exc:  # noqa: BLE001
        check(f"wrapper {label}", False, f"{type(exc).__name__}: {exc}")
        return
    new = wdb.rows[1:]
    ok = bool(new) and all(recompute_from_row(r, hash_raw_data) == r["current_hash"] for r in new)
    ok = ok and svc.verify_chain_links(wdb.rows) == [] and new[0]["prev_hash"] == "a" * 64
    if expect_command_prefix:
        ok = ok and all(str(r["command_id"]).startswith(expect_command_prefix) for r in new)
    ok = ok and any(q.endswith("FOR UPDATE") and "audit_chain_lock" in q for q in wdb.log)
    check(f"wrapper {label} writes via shared module", ok)


saved_stdin = sys.stdin
try:
    cd = load("control_device", "control_device")
    wrapper_case("control_device.write_audit", lambda d: cd.write_audit(
        d.cursor(), command_id="CMD_X", family_id=1, device_id="d", actor_id="u", actor_type="USER",
        action="CONTROL_DEVICE", status="SUCCEEDED", decision="ALLOW", reason="ok",
        parameters={"action": "lock"}, raw_data={"request": {"a": 1}}), hash_raw_data=True,
        expect_command_prefix="CMD_X")

    dsu = load("control_device", "device_status_update")
    wrapper_case("device_status_update.write_audit", lambda d: dsu.write_audit(
        d.cursor(), {"family_id": 1, "device_id": "d", "status": "SUCCEEDED", "command_id": "CMD_Y"},
        status="SUCCEEDED", decision="ALLOW", reason="DEVICE_STATUS_CALLBACK"), hash_raw_data=True,
        expect_command_prefix="CMD_Y")

    dash = load("dashboard", "get_family_dashboard")
    wrapper_case("get_family_dashboard.write_dashboard_audit", lambda d: dash.write_dashboard_audit(
        d.cursor(), audit_id="DASH_1", family_id=1, user_id="u", u_id=3, status="SUCCEEDED",
        decision="ALLOW", reason="DASHBOARD_LOADED", payload={"user_id": "u"}, result_summary={"n": 1}),
        hash_raw_data=True, expect_command_prefix="DASH_1")

    dec = load("decommission_device", "decommission_device")
    wrapper_case("decommission_device.insert_audit_log", lambda d: dec.insert_audit_log(
        d.cursor(), operator_user_id="admin", device_id="d", action="UC2.3_DEVICE_DECOMMISSION",
        parameters={"reason": "x"}, status="Verified", family_id=1),
        expect_command_prefix="UC2.3_DEVICE_DECOMMISSION-")

    dp = load("device_pair", "device_pair")
    wrapper_case("device_pair.append_audit_log", lambda d: dp.append_audit_log(
        d.cursor(), "admin", 7, "d", {"uc": "UC2.1"}, family_id=1), expect_command_prefix="tx-")

    gc = load("gateway_collaboration", "gateway_common")
    wrapper_case("gateway_common.append_cross_family_audit_logs", lambda d: gc.append_cross_family_audit_logs(
        d.cursor(), user_id="admin", gateway_a={"family_id": 1}, gateway_b={"family_id": 2},
        action="GATEWAY_TRUST_CREATED", parameters={"x": 1}), expect_command_prefix="tx-")

    gi = load("gateway_initialization", "gateway_init_common")
    wrapper_case("gateway_init_common.append_audit_log", lambda d: gi.append_audit_log(
        d.cursor(), user={"user_id": "admin", "id": 7}, family_id=1, action="GATEWAY_INITIALIZED",
        parameters={"x": 1}), expect_command_prefix="tx-")

    mm = load("maintenance_mode", "maintenance_mode")
    wrapper_case("maintenance_mode.append_audit_log", lambda d: mm.append_audit_log(
        d.cursor(), actor_id="admin", device_id="d", family_id=1, action="MAINTENANCE_MODE_ENABLED",
        status="Active", decision="ALLOW", reason="r", parameters={"duration_minutes": 5}),
        expect_command_prefix="MTN_")

    ota = load("ota_update", "ota_update")
    wrapper_case("ota_update.append_audit_log", lambda d: ota.append_audit_log(
        d.cursor(), actor_id="admin", device_id="d", family_id=1, status="Published", decision="ALLOW",
        reason="OTA_MQTT_PUBLISHED", parameters={"version": "1"}), expect_command_prefix="OTA_")

    br = load("control_device", "mqtt_topic_bridge")

    def run_bridge(d):
        br.get_db_connection = lambda: d
        br.write_event_audit("d", 1, "doorbell")
        if d.commits != 1 or len(d.rows) != 2:
            raise AssertionError("bridge did not write + commit exactly one row")
    wrapper_case("mqtt_topic_bridge.write_event_audit", run_bridge, expect_command_prefix="EVT_")
except ImportError as exc:
    check("import endpoint modules (needs pymysql, python-dotenv, cryptography, paho-mqtt)", False, str(exc))
finally:
    sys.stdin = saved_stdin

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
