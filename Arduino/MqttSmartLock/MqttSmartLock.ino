/*
 * SMART-LOCK-V1 測試韌體 — 對接 mqtt-server
 *
 * 流程：
 *   1. 連 WiFi → 連 MQTT broker
 *   2. 發送註冊 home/register {"model": "...", "mac": "..."}
 *   3. 收 server 回傳的 home/device/<mac>/config（retained）
 *   4. 監聽 home/device/<mac>/cmd（unlock / lock / doorbell_ack）
 *   5. 回報 home/device/<mac>/state {"locked": bool}
 *   6. 觸控腳觸發 home/device/<mac>/event（doorbell / tamper_detected）
 *
 * 硬體（測試用，不需外接零件）：
 *   GPIO2  = 狀態燈（開鎖時亮）
 *   GPIO15 = relay 替代 LED（開鎖時亮）
 *   GPIO27 = 門鈴觸控腳（T7，手指碰觸即觸發）
 *   GPIO32 = 防拆觸控腳（T9）
 *
 * 需要安裝程式庫：
 *   - PubSubClient（by Nick O'Leary，程式庫管理員搜尋即可）
 *   - Crypto（by Rhys Weatherley，提供 Ed25519.h，OTA 簽章驗證用）
 * HTTPClient / Update / mbedtls 都是 ESP32 core 內建，不用額外裝。
 */
#include <WiFi.h>
#include <PubSubClient.h>
#include <HTTPClient.h>
#include <Update.h>
#include <Ed25519.h>
#include "mbedtls/sha256.h"

// ====== 請修改這裡 ======
const char* WIFI_SSID = "SSID";
const char* WIFI_PASS = "PWD";
const char* MQTT_BROKER = "192.168.1.3";  // 跑 mosquitto 的電腦 IP
const int   MQTT_PORT = 1883;
// ========================

// OTA 簽章公鑰 —— 由 mqtt-server/ota_keys/generate_keypair.py 產生，
// 貼到這裡就好，這是公鑰不是私鑰，可以放心進版控。
const uint8_t OTA_PUBLIC_KEY[32] = { 0x3e, 0xce, 0x9b, 0xb3, 0x24, 0xd5, 0x3b, 0x27, 0xa6, 0x99, 0x10, 0x5e, 0xc9, 0x99, 0xcb, 0x81, 0xd8, 0x00, 0xa3, 0xc3, 0x09, 0x39, 0x94, 0x8d, 0xf6, 0x3f, 0x17, 0xea, 0x36, 0xd0, 0x14, 0x22 };

#define MODEL "SMART-LOCK-V1"
#define FW_VERSION "1.0.0"  // 每次燒錄新版本記得改，OTA 後可從序列埠確認是否真的更新成功

// 腳位（測試板：relay 用 LED 代替，按鈕用觸控腳代替）
#define PIN_RELAY_LED   15  // relay 替代 LED
#define PIN_STATUS_LED  2   // 狀態燈
#define TOUCH_DOORBELL  T7  // GPIO27
#define TOUCH_TAMPER    T9  // GPIO32
// 觸摸判定：開機時取樣當基準值，讀值偏離基準 1/3 以上視為觸摸
// （不同 core 版本數值範圍差很大：舊 core 沒碰約 60~80 碰到會變小；core 3.x 是大數值）
uint32_t doorbellBase = 0, tamperBase = 0;

uint32_t touchBaseline(uint8_t pin) {
  uint32_t sum = 0;
  for (int i = 0; i < 16; i++) { sum += touchRead(pin); delay(10); }
  return sum / 16;
}

bool isTouched(uint8_t pin, uint32_t base) {
  uint32_t v = touchRead(pin);
  return v < base - base / 3 || v > base + base / 3;
}

WiFiClient espClient;
PubSubClient mqtt(espClient);

String macAddr;          // 例如 "A4:CF:12:34:56:78"
String topicConfig, topicCmd, topicState, topicEvent, topicOta;

bool locked = true;           // 預設鎖上（models.yaml: default_state: locked）
int  autoLockSec = 5;         // 會被 server 回傳的 config 覆蓋
unsigned long unlockAt = 0;   // 開鎖時間，用來計算自動上鎖
bool registered = false;     // 本次開機是否已送過 register，斷線重連不再重送

// ---------- 狀態控制 ----------
void applyLockState(bool newLocked) {
  locked = newLocked;
  digitalWrite(PIN_RELAY_LED, locked ? LOW : HIGH);
  digitalWrite(PIN_STATUS_LED, locked ? LOW : HIGH);
  if (!locked) unlockAt = millis();

  String payload = String("{\"locked\":") + (locked ? "true" : "false") + "}";
  mqtt.publish(topicState.c_str(), payload.c_str());
  Serial.println("[STATE] " + payload);
}

void publishEvent(const char* type) {
  String payload = String("{\"type\":\"") + type + "\"}";
  mqtt.publish(topicEvent.c_str(), payload.c_str());
  Serial.println("[EVENT] " + payload);
}

// ---------- OTA 更新（下載韌體 → 邊寫入邊算 SHA-256 → 驗證 Ed25519 簽章 → 通過才切換分區）----------

// 下載固定 64 bytes 的簽章檔（伺服器對韌體 SHA-256 雜湊值的 Ed25519 簽章）
bool downloadSignature(const String& url, uint8_t* out64) {
  HTTPClient http;
  http.begin(url);
  int code = http.GET();
  if (code != HTTP_CODE_OK) {
    Serial.printf("[OTA] 簽章檔下載失敗，HTTP %d\n", code);
    http.end();
    return false;
  }
  int len = http.getSize();
  if (len != 64) {
    Serial.printf("[OTA] 簽章檔大小不對(%d，應為 64)，可能還沒 sign_firmware.py 簽過\n", len);
    http.end();
    return false;
  }
  WiFiClient* stream = http.getStreamPtr();
  int got = 0;
  unsigned long t0 = millis();
  while (got < 64 && millis() - t0 < 10000) {
    if (stream->available()) {
      int n = stream->read(out64 + got, 64 - got);
      if (n > 0) got += n;
    }
  }
  http.end();
  return got == 64;
}

void handleOta(const String& msg) {
  int idx = msg.indexOf("\"url\":\"");
  if (idx < 0) {
    Serial.println("[OTA] 訊息裡找不到 url，略過");
    return;
  }
  int start = idx + 7;
  int end = msg.indexOf('"', start);
  String url = msg.substring(start, end);
  String sigUrl = url + ".sig";

  Serial.println("[OTA] 下載簽章: " + sigUrl);
  uint8_t signature[64];
  if (!downloadSignature(sigUrl, signature)) {
    Serial.println("[OTA] 拿不到簽章檔，中止更新（沒簽章的韌體一律拒絕）");
    return;
  }

  Serial.println("[OTA] 開始下載韌體: " + url);
  HTTPClient http;
  http.begin(url);
  int httpCode = http.GET();
  if (httpCode != HTTP_CODE_OK) {
    Serial.printf("[OTA] 下載韌體失敗，HTTP %d\n", httpCode);
    http.end();
    return;
  }

  int contentLength = http.getSize();
  if (contentLength <= 0) {
    Serial.println("[OTA] 伺服器沒回傳韌體大小，中止");
    http.end();
    return;
  }

  if (!Update.begin(contentLength)) {
    Serial.printf("[OTA] Update.begin 失敗: %s\n", Update.errorString());
    http.end();
    return;
  }

  // 邊下載邊寫進 OTA 分區、邊累加 SHA-256，整包韌體不會同時放進記憶體
  mbedtls_sha256_context sha_ctx;
  mbedtls_sha256_init(&sha_ctx);
  mbedtls_sha256_starts(&sha_ctx, 0);  // 0 = SHA-256（不是 SHA-224）

  WiFiClient* stream = http.getStreamPtr();
  uint8_t buf[512];
  int written = 0;
  while (written < contentLength && http.connected()) {
    size_t avail = stream->available();
    if (!avail) { delay(1); continue; }
    int n = stream->read(buf, min(avail, sizeof(buf)));
    if (n <= 0) continue;
    Update.write(buf, n);
    mbedtls_sha256_update(&sha_ctx, buf, n);
    written += n;
  }
  http.end();

  if (written != contentLength) {
    Serial.printf("[OTA] 下載不完整 (%d/%d bytes)，中止\n", written, contentLength);
    Update.abort();
    mbedtls_sha256_free(&sha_ctx);
    return;
  }

  uint8_t digest[32];
  mbedtls_sha256_finish(&sha_ctx, digest);
  mbedtls_sha256_free(&sha_ctx);

  Serial.print("[OTA] 韌體 SHA-256: ");
  for (int i = 0; i < 32; i++) Serial.printf("%02x", digest[i]);
  Serial.println();

  if (!Ed25519::verify(signature, OTA_PUBLIC_KEY, digest, sizeof(digest))) {
    Serial.println("[OTA] 簽章驗證失敗！韌體可能被竄改或不是官方簽發，拒絕更新");
    Update.abort();  // 沒切換開機分區，重開機還是跑原本的韌體，不會變磚
    return;
  }

  Serial.println("[OTA] 簽章驗證通過，寫入完成，準備重開機");
  if (!Update.end(true)) {
    Serial.printf("[OTA] Update.end 失敗: %s\n", Update.errorString());
    return;
  }
  ESP.restart();
}

// ---------- MQTT 訊息處理 ----------
void onMqttMessage(char* topic, byte* payload, unsigned int length) {
  String msg;
  for (unsigned int i = 0; i < length; i++) msg += (char)payload[i];
  Serial.println("[RECV] " + String(topic) + " → " + msg);

  if (topicOta.equals(topic)) {
    handleOta(msg);
    return;
  }

  if (topicConfig.equals(topic)) {
    // 只取 auto_lock_sec，其他設定目前用不到
    int idx = msg.indexOf("\"auto_lock_sec\":");
    if (idx >= 0) {
      autoLockSec = msg.substring(idx + 16).toInt();
      Serial.println("[CONFIG] auto_lock_sec = " + String(autoLockSec));
    }
    return;
  }

  if (topicCmd.equals(topic)) {
    if (msg.indexOf("\"unlock\"") >= 0) {         // 注意："unlock" 要先判斷（含 "lock" 字串）
      applyLockState(false);
    } else if (msg.indexOf("\"lock\"") >= 0) {
      applyLockState(true);
    } else if (msg.indexOf("\"doorbell_ack\"") >= 0) {
      Serial.println("[CMD] 門鈴已確認");
    }
  }
}

// ---------- 連線 ----------
void connectMqtt() {
  while (!mqtt.connected()) {
    Serial.print("[MQTT] 連線 " + String(MQTT_BROKER) + " ... ");
    String clientId = "esp32-lock-" + macAddr;
    if (mqtt.connect(clientId.c_str())) {
      Serial.println("成功");
      mqtt.subscribe(topicConfig.c_str());
      mqtt.subscribe(topicCmd.c_str());
      mqtt.subscribe(topicOta.c_str());

      // 發送註冊，server 會回 config（retained）— 已註冊過就不重送，避免斷線重連時重複註冊
      if (!registered) {
        String reg = String("{\"model\":\"") + MODEL + "\",\"mac\":\"" + macAddr + "\"}";
        mqtt.publish("home/register", reg.c_str());
        Serial.println("[REGISTER] " + reg);
        registered = true;
      } else {
        Serial.println("[REGISTER] 已註冊過，略過");
      }

      // 回報目前狀態
      applyLockState(locked);
    } else {
      Serial.println("失敗 rc=" + String(mqtt.state()) + "，3 秒後重試");
      delay(3000);
    }
  }
}

void setup() {
  Serial.begin(115200);
  pinMode(PIN_RELAY_LED, OUTPUT);
  pinMode(PIN_STATUS_LED, OUTPUT);
  digitalWrite(PIN_RELAY_LED, LOW);
  digitalWrite(PIN_STATUS_LED, LOW);

  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  Serial.print("[WiFi] 連線中");
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  macAddr = WiFi.macAddress();
  Serial.println("\n[WiFi] IP: " + WiFi.localIP().toString() + "  MAC: " + macAddr);
  Serial.println("[BOOT] 韌體版本 " FW_VERSION);

  topicConfig = "home/device/" + macAddr + "/config";
  topicCmd    = "home/device/" + macAddr + "/cmd";
  topicState  = "home/device/" + macAddr + "/state";
  topicEvent  = "home/device/" + macAddr + "/event";
  topicOta    = "home/device/" + macAddr + "/ota";

  // 觸控腳校準（此時不要碰 GPIO27 / GPIO32）
  doorbellBase = touchBaseline(TOUCH_DOORBELL);
  tamperBase   = touchBaseline(TOUCH_TAMPER);
  Serial.println("[TOUCH] 基準值 doorbell(GPIO27)=" + String(doorbellBase) +
                 "  tamper(GPIO32)=" + String(tamperBase));

  mqtt.setServer(MQTT_BROKER, MQTT_PORT);
  mqtt.setCallback(onMqttMessage);
  connectMqtt();
}

void loop() {
  if (!mqtt.connected()) connectMqtt();
  mqtt.loop();

  // 自動上鎖
  if (!locked && millis() - unlockAt >= (unsigned long)autoLockSec * 1000) {
    Serial.println("[AUTO] 超過 " + String(autoLockSec) + " 秒，自動上鎖");
    applyLockState(true);
  }

  // 門鈴：觸摸 GPIO27（T7），500ms 冷卻避免連發
  static unsigned long lastDoorbell = 0;
  if (isTouched(TOUCH_DOORBELL, doorbellBase) && millis() - lastDoorbell > 500) {
    lastDoorbell = millis();
    publishEvent("doorbell");
  }

  // 防拆：觸摸 GPIO32（T9），2 秒冷卻
  static unsigned long lastTamper = 0;
  if (isTouched(TOUCH_TAMPER, tamperBase) && millis() - lastTamper > 2000) {
    lastTamper = millis();
    publishEvent("tamper_detected");
  }

  // 每 2 秒印一次觸控讀值，方便校準（確認 OK 後可以刪掉這段）
  static unsigned long lastDebug = 0;
  if (millis() - lastDebug > 2000) {
    lastDebug = millis();
    Serial.println("[TOUCH] doorbell=" + String(touchRead(TOUCH_DOORBELL)) +
                   " (base " + String(doorbellBase) + ")  tamper=" +
                   String(touchRead(TOUCH_TAMPER)) + " (base " + String(tamperBase) + ")");
  }

  delay(10);
}
