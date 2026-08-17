#!/usr/bin/env bash
# 產生 MQTT TLS 用的自簽 CA + mosquitto broker 憑證。
#
# 用法： ./generate_certs.sh <mosquitto 所在機器的區網 IP>
# 例：   ./generate_certs.sh 192.168.1.3
#
# CA 只會產生一次（第一次跑之後檔案就存在，之後重跑不會覆蓋）。
# 換機器/換區網 IP 時只需要重新簽 server 憑證，CA 不變 ——
# ESP32 sketch 裡貼的 MQTT_ROOT_CA 不用重新燒錄，除非 CA 本身要換。
set -euo pipefail
cd "$(dirname "$0")"

LAN_IP="${1:?用法: ./generate_certs.sh <區網IP>，例如 ./generate_certs.sh 192.168.1.3}"

if [ ! -f ca.key ]; then
  echo "產生 CA（10 年效期，私鑰只留在本機）..."
  openssl genrsa -out ca.key 2048
  openssl req -x509 -new -nodes -key ca.key -sha256 -days 3650 \
    -subj "/CN=IOTA-API Local MQTT CA" -out ca.crt
else
  echo "ca.key / ca.crt 已存在，略過（不重新產生，避免既有裝置的信任失效）"
fi

echo "產生 server 憑證（SAN: IP:$LAN_IP, IP:127.0.0.1, DNS:mqtt-broker, DNS:localhost）..."
openssl genrsa -out server.key 2048
openssl req -new -key server.key -subj "/CN=$LAN_IP" -out server.csr

cat > server.ext <<EOF
subjectAltName = IP:$LAN_IP,IP:127.0.0.1,DNS:mqtt-broker,DNS:localhost
EOF

openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial \
  -out server.crt -days 3650 -sha256 -extfile server.ext

rm -f server.csr server.ext ca.srl

chmod 644 ca.crt server.crt
chmod 600 ca.key server.key 2>/dev/null || true

echo ""
echo "完成，config/certs/ 底下產生了："
echo "  ca.crt     — CA 公開憑證：mosquitto 用，也要貼進 Arduino sketch 的 MQTT_ROOT_CA（可進版控）"
echo "  ca.key     — CA 私鑰：絕對不要外流、不要進版控（已在 .gitignore）"
echo "  server.crt — mosquitto 的憑證，SAN 含 $LAN_IP（可進版控）"
echo "  server.key — mosquitto 的私鑰（已在 .gitignore）"
echo ""
echo "下一步：把 ca.crt 的內容貼進 Arduino/MqttSmartLock/MqttSmartLock.ino 的 MQTT_ROOT_CA。"
