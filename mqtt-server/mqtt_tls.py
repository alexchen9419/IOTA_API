"""共用的 MQTT TLS 設定，給 main.py / web_monitor.py / test_*.py 用。

broker 現在只開 TLS（8883），沒有 TLS 就連不上。`MQTT_CA_CERT` 指向掛進容器的
config/certs/ca.crt；本機（非容器）跑的話可以留空，`tls_set()` 不帶參數會走系統
預設信任庫（連不上我們的自簽 CA，僅供本機直接對容器 debug 時退而求其次）。
"""
import os


def apply_tls(client) -> None:
    if os.getenv("MQTT_USE_TLS", "1") != "1":
        return
    ca_cert = os.getenv("MQTT_CA_CERT")
    if ca_cert:
        client.tls_set(ca_certs=ca_cert)
    else:
        client.tls_set()


def broker_port() -> int:
    return int(os.getenv("MQTT_PORT", "8883"))
