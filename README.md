# IoT-MQTT-Env
1. 伺服器核心參數
主機位址 (Host)：localhost (127.0.0.1)

通訊埠 (Port)：1883

身分驗證 (Auth)：目前為匿名登入 (不需帳號密碼)

通訊協議：MQTT v3.1.1 標準協議

2. 系統開發架構
功能定義：作為後端 API（如 Gateway 核心）與終端硬體設備（如鎖具、感測器）之間的操作指令中轉站。

安全審計：所有透過此環境傳輸的指令訊息，將與 IOTA Tangle 進行整合，確保審計日誌的不可篡改性。

主題規範 (Topic Tree)：

核心主題路徑：home/security/#

裝置範例：home/security/LOCK_01

3. 必要組件說明
服務核心：基於 Mosquitto MQTT Broker。

配置管理：使用 Docker 進行容器化封裝，確保專題小組成員間的開發環境一致。