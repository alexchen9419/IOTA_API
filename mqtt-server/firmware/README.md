# OTA 韌體檔案

把要推送的編譯好 `.bin` 放這裡，server 會透過 `http://<host>:8080/firmware/<檔名>` 提供下載。

命名建議：`<model>_<version>.bin`，例如 `SMART-LOCK-V1_1.1.0.bin`。

Arduino IDE 匯出方式：草稿碼 → 「輸出編譯後的二進位檔」，產生的 `.ino.bin` 複製過來改名即可。

## 簽章（必要，韌體端會拒絕沒簽章的更新）

放進來的 `.bin` 一定要簽過名才推得動，否則 ESP32 下載完會驗證失敗、直接拒絕更新：

```bash
docker exec mqtt_server python sign_firmware.py firmware/SMART-LOCK-V1_1.1.0.bin
```

會在旁邊產生同名的 `.bin.sig`（64 bytes），一起透過 `/firmware/` 提供下載。簽章私鑰在 `mqtt-server/ota_keys/ota_signing_key.pem`，第一次用之前要先跑 `ota_keys/generate_keypair.py` 產生一組，並把印出來的公鑰陣列貼進 Arduino sketch 的 `OTA_PUBLIC_KEY`（只需要貼一次，除非之後要換金鑰）。
