"""Exercise ota_update.check_firmware() with a throwaway keypair and temp firmware dir.

Run inside the api container (so the real code and cryptography version are used):
  Get-Content tools\\ota_check_test.py -Raw | docker exec -i iot_api python -
Nothing outside /tmp is touched; the real firmware dir and OTA key are not used.
"""
import hashlib
import os
import sys
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

tmp = Path(tempfile.mkdtemp())
fw = tmp / "fw"
fw.mkdir()
key = Ed25519PrivateKey.generate()
pub = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
pubfile = tmp / "ota_public_key.h"
pubfile.write_text("const uint8_t OTA_PUBLIC_KEY[32] = { " + ", ".join(f"0x{b:02x}" for b in pub) + " };\n")

os.environ["FIRMWARE_DIR"] = str(fw)
os.environ["OTA_PUBKEY_FILE"] = str(pubfile)
sys.path.insert(0, "/app/ota_update")
sys.path.insert(0, "/app")
import ota_update as o  # noqa: E402


def sign(name, data=b"firmware-bytes" * 100, sig=None):
    (fw / name).write_bytes(data)
    (fw / (name + ".sig")).write_bytes(sig if sig is not None else key.sign(hashlib.sha256(data).digest()))


results = []


def check(label, got, want):
    ok = (got is None and want is None) or (got is not None and want is not None and got[1] == want)
    results.append(ok)
    print(("PASS " if ok else "FAIL ") + f"{label:44} -> {got}")


sign("good.bin")
check("valid firmware + valid signature", o.check_firmware("good.bin"), None)

sign("tampered.bin")
(fw / "tampered.bin").write_bytes(b"evil" * 100)
check("bin changed after signing", o.check_firmware("tampered.bin"), 409)

sign("othersig.bin", sig=Ed25519PrivateKey.generate().sign(b"x" * 32))
check("signed by a different key", o.check_firmware("othersig.bin"), 409)

sign("shortsig.bin", sig=b"\x00" * 10)
check("signature file is not 64 bytes", o.check_firmware("shortsig.bin"), 409)

(fw / "nosig.bin").write_bytes(b"abc")
check("missing .sig", o.check_firmware("nosig.bin"), 409)
check("missing .bin", o.check_firmware("nothere.bin"), 404)

(tmp / "outside.bin").write_bytes(b"abc")
check("path traversal ../outside.bin", o.check_firmware("../outside.bin"), 400)
check("absolute path", o.check_firmware(str(tmp / "outside.bin")), 400)

o.FIRMWARE_DIR = tmp / "does-not-exist"
check("firmware dir not mounted", o.check_firmware("good.bin"), 503)

print(f"\n{sum(results)}/{len(results)} checks passed")
sys.exit(0 if all(results) else 1)
