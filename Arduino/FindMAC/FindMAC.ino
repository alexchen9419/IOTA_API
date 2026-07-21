#include "WiFi.h"

#define led1 2
#define led2 15

// LED1 的 Task（跑在 Core 0）
void taskLed1(void *pvParameters) {
  while (true) {
    digitalWrite(led1, 1);
    vTaskDelay(pdMS_TO_TICKS(1000));  // 不用 delay()，用 RTOS 版本
    digitalWrite(led1, 0);
    vTaskDelay(pdMS_TO_TICKS(1000));
  }
}

// LED2 的 Task（跑在 Core 1）
void taskLed2(void *pvParameters) {
  while (true) {
    digitalWrite(led2, 0);
    vTaskDelay(pdMS_TO_TICKS(200));   // 不同頻率，互不干擾
    digitalWrite(led2, 1);
    vTaskDelay(pdMS_TO_TICKS(200));
  }
}

void setup() {
  pinMode(led1, OUTPUT);
  pinMode(led2, OUTPUT);
  Serial.begin(115200);

  WiFi.mode(WIFI_MODE_STA);
  delay(100);
  Serial.println("MAC ADDR:" + WiFi.macAddress());

  // 建立 Task
  xTaskCreatePinnedToCore(
    taskLed1,    // Task 函式
    "LED1 Task", // 名稱（debug 用）
    1024,        // Stack 大小 (bytes)
    NULL,        // 參數
    1,           // 優先級（0最低）
    NULL,        // Task handle
    0            // Core 0
  );

  xTaskCreatePinnedToCore(
    taskLed2,
    "LED2 Task",
    1024,
    NULL,
    1,
    NULL,
    1            // Core 1
  );
}

void loop() {
  // 空著就好，Task 自己跑
  vTaskDelay(pdMS_TO_TICKS(1000));
}