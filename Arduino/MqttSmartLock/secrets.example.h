// 範本：複製成同資料夾的 secrets.h 再填值。secrets.h 已在 .gitignore，不會進版控。
// 這份範本只放佔位字串，不要填真的帳密。
#pragma once

const char* WIFI_SSID = "SSID";
const char* WIFI_PASS = "PWD";

// 跑 mosquitto 的電腦區網 IP，要跟 repo 根目錄 .env 的 OTA_HOST 是同一台機器
const char* MQTT_BROKER = "192.168.0.84";

// broker 已關閉匿名。帳號固定是 device，密碼 = .env 的 MQTT_DEVICE_PASSWORD
const char* MQTT_USER = "device";
const char* MQTT_PASS = "CHANGE_ME";
