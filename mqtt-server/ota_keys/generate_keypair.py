"""一次性腳本：產生 OTA 韌體簽章用的 Ed25519 金鑰對。

私鑰只留在這個資料夾（已加進 .gitignore，不會進版控），
公鑰要貼進 Arduino/MqttSmartLock/MqttSmartLock.ino 的 OTA_PUBLIC_KEY。

用法：docker exec mqtt_server python ota_keys/generate_keypair.py
"""
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

_DIR = Path(__file__).parent
PRIVATE_KEY_FILE = _DIR / "ota_signing_key.pem"
PUBLIC_KEY_FILE = _DIR / "ota_public_key.h"

if PRIVATE_KEY_FILE.exists():
    raise SystemExit(f"{PRIVATE_KEY_FILE} 已存在，不要覆蓋既有金鑰（會讓舊簽章失效）。要重新產生請先手動刪除。")

private_key = Ed25519PrivateKey.generate()
public_key = private_key.public_key()

PRIVATE_KEY_FILE.write_bytes(
    private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
)

raw_public = public_key.public_bytes(
    encoding=serialization.Encoding.Raw,
    format=serialization.PublicFormat.Raw,
)

c_array = ", ".join(f"0x{b:02x}" for b in raw_public)
header = f"""// 自動產生，貼進 Arduino sketch 用。不要把私鑰貼進來，這裡只有公鑰。
// 由 ota_keys/generate_keypair.py 產生於 {PRIVATE_KEY_FILE.parent}
const uint8_t OTA_PUBLIC_KEY[32] = {{ {c_array} }};
"""
PUBLIC_KEY_FILE.write_text(header, encoding="utf-8")

print(f"私鑰已寫入 {PRIVATE_KEY_FILE}（不要外流、不要進版控）")
print(f"公鑰 C 陣列已寫入 {PUBLIC_KEY_FILE}，內容如下，複製貼到 Arduino sketch：\n")
print(header)
