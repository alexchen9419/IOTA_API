# OTA 韌體檔案

把要推送的編譯好 `.bin` 放這裡，server 會透過 `http://<host>:8080/firmware/<檔名>` 提供下載。

命名建議：`<model>_<version>.bin`，例如 `SMART-LOCK-V1_1.1.0.bin`。

Arduino IDE 匯出方式：草稿碼 → 「輸出編譯後的二進位檔」，產生的 `.ino.bin` 複製過來改名即可。
