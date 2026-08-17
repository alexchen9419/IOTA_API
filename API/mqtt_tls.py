"""共用的 MQTT TLS 設定，給 control_device.py / mqtt_status_worker.py /
mqtt_topic_bridge.py / generate_guest_qr.py / respond_invitation.py /
update_member_role.py 用。

broker 現在只開 TLS（8883），沒有 TLS 就連不上。`MQTT_CA_CERT` 指向掛進容器的
config/certs/ca.crt（docker-compose 已設好 PYTHONPATH=/app，讓 CGI 子行程不管
自己的 cwd 是哪個子目錄都能 import 到這支模組）。
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


def publish_tls_kwargs() -> dict:
    """給 paho.mqtt.publish.single()/multiple() 用的 tls= 參數。"""
    if os.getenv("MQTT_USE_TLS", "1") != "1":
        return {}
    ca_cert = os.getenv("MQTT_CA_CERT")
    return {"tls": {"ca_certs": ca_cert}} if ca_cert else {"tls": {}}
