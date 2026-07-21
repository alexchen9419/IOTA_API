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
 * 需要安裝程式庫：PubSubClient（by Nick O'Leary，程式庫管理員搜尋即可）
 */
#include <WiFi.h>
#include <PubSubClient.h>

// ====== 請修改這裡 ======
const char* WIFI_SSID = "TOTOLINK_A8004T_5G";
const char* WIFI_PASS = "sherry8088";
const char* MQTT_BROKER = "192.168.1.3";  // 跑 mosquitto 的電腦 IP
const int   MQTT_PORT = 1883;
// ========================

#define MODEL "SMART-LOCK-V1"

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
String topicConfig, topicCmd, topicState, topicEvent;

bool locked = true;           // 預設鎖上（models.yaml: default_state: locked）
int  autoLockSec = 5;         // 會被 server 回傳的 config 覆蓋
unsigned long unlockAt = 0;   // 開鎖時間，用來計算自動上鎖

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

// ---------- MQTT 訊息處理 ----------
void onMqttMessage(char* topic, byte* payload, unsigned int length) {
  String msg;
  for (unsigned int i = 0; i < length; i++) msg += (char)payload[i];
  Serial.println("[RECV] " + String(topic) + " → " + msg);

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

      // 發送註冊，server 會回 config（retained）
      String reg = String("{\"model\":\"") + MODEL + "\",\"mac\":\"" + macAddr + "\"}";
      mqtt.publish("home/register", reg.c_str());
      Serial.println("[REGISTER] " + reg);

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

  topicConfig = "home/device/" + macAddr + "/config";
  topicCmd    = "home/device/" + macAddr + "/cmd";
  topicState  = "home/device/" + macAddr + "/state";
  topicEvent  = "home/device/" + macAddr + "/event";

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
