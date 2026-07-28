"""簽署 OTA 韌體檔案：對 .bin 的 SHA-256 雜湊值做 Ed25519 簽章，輸出同名 .sig。

裝置端（Arduino sketch）下載韌體時會邊下載邊算 SHA-256（避免整包放進記憶體），
下載完再用內建公鑰驗證這個 .sig，驗證失敗就不會切換開機分區、不會真的更新。

用法：docker exec mqtt_server python sign_firmware.py firmware/SMART-LOCK-V1_1.1.0.bin
"""
import hashlib
import sys
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

_DIR = Path(__file__).parent
PRIVATE_KEY_FILE = _DIR / "ota_keys" / "ota_signing_key.pem"


def load_private_key() -> Ed25519PrivateKey:
    if not PRIVATE_KEY_FILE.exists():
        raise SystemExit(
            f"找不到簽章私鑰 {PRIVATE_KEY_FILE}，先跑 ota_keys/generate_keypair.py 產生一組。"
        )
    return serialization.load_pem_private_key(PRIVATE_KEY_FILE.read_bytes(), password=None)


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("用法: python sign_firmware.py <韌體檔案路徑>")

    firmware_path = Path(sys.argv[1])
    if not firmware_path.exists():
        raise SystemExit(f"找不到韌體檔案: {firmware_path}")

    digest = hashlib.sha256(firmware_path.read_bytes()).digest()
    private_key = load_private_key()
    signature = private_key.sign(digest)  # Ed25519 對這 32 bytes 雜湊值簽章

    sig_path = firmware_path.with_suffix(firmware_path.suffix + ".sig")
    sig_path.write_bytes(signature)

    print(f"已簽署: {firmware_path}")
    print(f"SHA-256: {digest.hex()}")
    print(f"簽章檔: {sig_path}（64 bytes，會跟 .bin 一起透過 /firmware/ 提供下載）")


if __name__ == "__main__":
    main()
