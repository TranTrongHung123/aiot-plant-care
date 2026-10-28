#include <Arduino.h>
#include <Wire.h>
#include <WiFi.h>
#include <time.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <Adafruit_BMP085.h>
#include <DHT.h>

#if __has_include("secrets.h")
#include "secrets.h"
#else
#include "secrets.example.h"
#endif

// --- CẤU HÌNH CHÂN PHẦN CỨNG ---
#define PUMP_PIN    16   // Relay IN1 (GPIO 16 / RX2)
#define LED_PIN     2    // Đèn LED xanh trên bo ESP32
#define DHTPIN      4    // Chân Data DHT22 (GPIO 4 / D4)
#define DHTTYPE     DHT22
#define SOIL_PIN    34   // Chân Analog A0 cảm biến đất (GPIO 34 / D34 - ADC1)
#define I2C_SDA     21   // Chân I2C SDA (GPIO 21 / D21)
#define I2C_SCL     22   // Chân I2C SCL (GPIO 22 / D22)

// Logic Relay Kích mức Cao (Jumper S1 cắm ở [H])
#define RELAY_ON    HIGH
#define RELAY_OFF   LOW

// Giới hạn an toàn tối đa mỗi lần bơm chạy (15 giây)
#define MAX_PUMP_DURATION_MS 15000UL

// --- HỢP ĐỒNG MQTT VỚI BACKEND ---
static const char* DEVICE_ID       = "sim-esp32-01";
static const char* SOURCE_NAME     = "simulation";
static const char* TOPIC_TELEMETRY = "plantcare/v1/simulation/sim-esp32-01/telemetry";
static const char* TOPIC_STATUS    = "plantcare/v1/simulation/sim-esp32-01/status";
static const char* TOPIC_ACK       = "plantcare/v1/simulation/sim-esp32-01/ack";
static const char* TOPIC_COMMAND   = "plantcare/v1/simulation/sim-esp32-01/command";

// Màn hình OLED SSD1306 128x64
#define SCREEN_WIDTH 128
#define SCREEN_HEIGHT 64
Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, -1);

// Cảm biến & Mạng
DHT dht(DHTPIN, DHTTYPE);
Adafruit_BMP085 bmp;
WiFiClient wifiClient;
PubSubClient mqttClient(wifiClient);

// Trạng thái toàn cục
bool oledAvailable = false;
bool bmpAvailable = false;
bool ntpSynced = false;
bool pumpState = false;
unsigned long pumpStopAtMs = 0;
String activeCommandId = "";

String bootId = "";
uint32_t sequenceNumber = 0;
unsigned long lastTelemetryMs = 0;
unsigned long lastDisplayMs = 0;
unsigned long lastReconnectAttemptMs = 0;

float lastValidTemp = 28.0f;
float lastValidHum  = 65.0f;
float lastPressHpa  = 1013.25f;
int lastSoilRaw     = 4095;
int lastSoilPct     = 0;

// Sinh UUID v4 chuẩn RFC 4122 từ phần cứng RNG của ESP32
String generateUuidV4() {
    uint8_t bytes[16];
    for (int i = 0; i < 16; i += 4) {
        uint32_t r = esp_random();
        bytes[i]     = (r >> 24) & 0xFF;
        bytes[i + 1] = (r >> 16) & 0xFF;
        bytes[i + 2] = (r >> 8) & 0xFF;
        bytes[i + 3] = r & 0xFF;
    }
    bytes[6] = (bytes[6] & 0x0F) | 0x40; // Version 4
    bytes[8] = (bytes[8] & 0x3F) | 0x80; // Variant 10xx

    char uuid[37];
    snprintf(uuid, sizeof(uuid),
             "%02x%02x%02x%02x-%02x%02x-%02x%02x-%02x%02x-%02x%02x%02x%02x%02x%02x",
             bytes[0], bytes[1], bytes[2], bytes[3],
             bytes[4], bytes[5], bytes[6], bytes[7],
             bytes[8], bytes[9], bytes[10], bytes[11],
             bytes[12], bytes[13], bytes[14], bytes[15]);
    return String(uuid);
}

// Lấy chuỗi thời gian UTC chuẩn ISO-8601 (YYYY-MM-DDTHH:MM:SSZ)
String getIsoTimestamp() {
    time_t nowSec = time(nullptr);
    struct tm timeinfo;
    gmtime_r(&nowSec, &timeinfo);
    char buf[32];
    strftime(buf, sizeof(buf), "%Y-%m-%dT%H:%M:%SZ", &timeinfo);
    return String(buf);
}

bool isTimeSynced() {
    time_t nowSec = time(nullptr);
    if (nowSec > 1700000000) {
        ntpSynced = true;
        return true;
    }
    return false;
}

// Điều khiển phần cứng Bơm
void setPumpHardware(bool turnOn) {
    pumpState = turnOn;
    digitalWrite(PUMP_PIN, turnOn ? RELAY_ON : RELAY_OFF);
    digitalWrite(LED_PIN, turnOn ? HIGH : LOW);
}

// Xuất bản trạng thái thiết bị (online / pump) lên MQTT
void publishStatus(bool online = true, const char* customPump = nullptr) {
    if (!mqttClient.connected() || !isTimeSynced()) {
        return;
    }
    JsonDocument doc;
    doc["schema_version"] = 1;
    doc["device_id"]      = DEVICE_ID;
    doc["source"]         = SOURCE_NAME;
    doc["timestamp"]      = getIsoTimestamp();
    doc["online"]         = online;
    doc["pump"]           = customPump ? customPump : (pumpState ? "on" : "off");
    doc["scenario"]       = "normal";

    char buffer[384];
    size_t len = serializeJson(doc, buffer, sizeof(buffer));
    mqttClient.publish(TOPIC_STATUS, (const uint8_t*)buffer, len, true);
}

// Xuất bản phản hồi ACK cho lệnh điều khiển bơm
void publishAck(const String& commandId, const char* status, const char* reason) {
    if (!mqttClient.connected() || !isTimeSynced() || commandId.length() == 0) {
        return;
    }
    JsonDocument doc;
    doc["schema_version"] = 1;
    doc["device_id"]      = DEVICE_ID;
    doc["source"]         = SOURCE_NAME;
    doc["command_id"]     = commandId;
    doc["timestamp"]      = getIsoTimestamp();
    doc["status"]         = status;
    doc["reason"]         = reason;

    char buffer[512];
    size_t len = serializeJson(doc, buffer, sizeof(buffer));
    mqttClient.publish(TOPIC_ACK, (const uint8_t*)buffer, len, false);
    Serial.printf(">> [MQTT ACK] cmd=%s | status=%s | reason=%s\n",
                  commandId.c_str(), status, reason);
}

// Xuất bản dữ liệu cảm biến (Telemetry)
void sampleAndPublishTelemetry() {
    float t = dht.readTemperature();
    float h = dht.readHumidity();
    if (!isnan(t) && t >= -20.0f && t <= 70.0f) {
        lastValidTemp = t;
    }
    if (!isnan(h) && h >= 0.0f && h <= 100.0f) {
        lastValidHum = h;
    }

    if (bmpAvailable) {
        float p = bmp.readPressure() / 100.0f;
        if (p >= 800.0f && p <= 1200.0f) {
            lastPressHpa = p;
        }
    }

    lastSoilRaw = analogRead(SOIL_PIN);
    lastSoilPct = map(constrain(lastSoilRaw, 1500, 4095), 4095, 1500, 0, 100);

    Serial.printf("[SENSOR] T=%.1fC | H=%.1f%% | P=%.1fhPa | Soil=%d (%d%%) | Pump=%s | MQTT=%s\n",
                  lastValidTemp, lastValidHum, lastPressHpa,
                  lastSoilRaw, lastSoilPct,
                  pumpState ? "ON" : "OFF",
                  mqttClient.connected() ? "OK" : "DISC");

    if (!mqttClient.connected() || !isTimeSynced()) {
        return;
    }

    sequenceNumber++;
    JsonDocument doc;
    doc["schema_version"] = 1;
    doc["message_id"]     = generateUuidV4();
    doc["device_id"]      = DEVICE_ID;
    doc["source"]         = SOURCE_NAME;
    doc["measured_at"]    = getIsoTimestamp();
    doc["boot_id"]        = bootId;
    doc["sequence"]       = sequenceNumber;

    JsonObject readings = doc["readings"].to<JsonObject>();
    readings["temperature_c"]     = roundf(lastValidTemp * 10.0f) / 10.0f;
    readings["humidity_pct"]      = roundf(lastValidHum * 10.0f) / 10.0f;
    readings["soil_moisture_raw"] = lastSoilRaw;
    readings["soil_moisture_pct"] = (float)lastSoilPct;
    readings["pressure_hpa"]      = roundf(lastPressHpa * 10.0f) / 10.0f;

    char buffer[640];
    size_t len = serializeJson(doc, buffer, sizeof(buffer));
    bool ok = mqttClient.publish(TOPIC_TELEMETRY, (const uint8_t*)buffer, len, false);
    publishStatus(true);
    Serial.printf(">> [MQTT PUB] Telemetry seq=%u (%u bytes) -> %s\n",
                  sequenceNumber, (unsigned int)len, ok ? "OK" : "FAILED");
}

// Xử lý lệnh điều khiển từ Backend gửi xuống
void onMqttMessage(char* topic, byte* payload, unsigned int length) {
    if (String(topic) != TOPIC_COMMAND) {
        return;
    }

    JsonDocument doc;
    DeserializationError err = deserializeJson(doc, payload, length);
    if (err) {
        Serial.printf("[MQTT ERR] Loi giai ma JSON command: %s\n", err.c_str());
        return;
    }

    String commandId = doc["command_id"] | "";
    String deviceId  = doc["device_id"] | "";
    String action    = doc["action"] | "";
    String expiresAt = doc["expires_at"] | "";
    long durationMs  = doc["duration_ms"] | 5000;

    if (commandId.length() == 0 || deviceId != DEVICE_ID) {
        return;
    }

    if (expiresAt.length() >= 19 && isTimeSynced()) {
        String nowIso = getIsoTimestamp().substring(0, 19);
        String expIso = expiresAt.substring(0, 19);
        if (nowIso > expIso) {
            publishAck(commandId, "rejected", "Lệnh điều khiển đã hết hạn");
            return;
        }
    }

    Serial.printf(">> [MQTT CMD] Nhan lenh action=%s | duration_ms=%ld | id=%s\n",
                  action.c_str(), durationMs, commandId.c_str());

    if (action == "pump_on") {
        unsigned long clampedMs = (unsigned long)constrain(durationMs, 1000L, (long)MAX_PUMP_DURATION_MS);
        setPumpHardware(true);
        activeCommandId = commandId;
        pumpStopAtMs = millis() + clampedMs;

        publishAck(commandId, "accepted", "ESP32 đã bật máy bơm");
        publishStatus(true, "on");
    } else if (action == "pump_off") {
        String prevCmd = activeCommandId;
        activeCommandId = "";
        pumpStopAtMs = 0;
        setPumpHardware(false);

        if (prevCmd.length() > 0 && prevCmd != commandId) {
            publishAck(prevCmd, "completed", "Đã dừng sớm theo lệnh tắt bơm");
        }
        publishAck(commandId, "accepted", "Đang tắt máy bơm");
        publishStatus(true, "off");
        publishAck(commandId, "completed", "ESP32 đã tắt máy bơm");
    } else {
        publishAck(commandId, "rejected", "Hành động không hợp lệ");
    }
}

// Hiển thị màn hình OLED 128x64
void renderDashboard() {
    if (!oledAvailable) {
        return;
    }
    display.clearDisplay();
    display.setTextColor(SSD1306_WHITE);
    display.setTextSize(1);

    // Dòng 1: Trạng thái Wi-Fi & MQTT
    display.setCursor(0, 0);
    if (WiFi.status() != WL_CONNECTED) {
        display.print("WIFI: DANG KET NOI...");
    } else if (!mqttClient.connected()) {
        display.print("WIFI:OK | MQTT:CHO...");
    } else {
        display.print("WIFI:OK | MQTT:ONLINE");
    }
    display.drawLine(0, 9, 128, 9, SSD1306_WHITE);

    // Dòng 2: Nhiệt độ & Độ ẩm KK
    display.setCursor(0, 12);
    display.printf("T: %.1fC | H: %.1f%%", lastValidTemp, lastValidHum);

    // Dòng 3: Độ ẩm đất
    display.setCursor(0, 23);
    display.printf("Dat: %d%% (Raw:%d)", lastSoilPct, lastSoilRaw);

    // Dòng 4: Áp suất khí quyển
    display.setCursor(0, 34);
    if (bmpAvailable) {
        display.printf("Ap suat: %.0f hPa", lastPressHpa);
    } else {
        display.print("Ap suat: N/A");
    }

    // Dòng 5: Trạng thái Bơm
    display.drawLine(0, 48, 128, 48, SSD1306_WHITE);
    display.setCursor(0, 53);
    if (pumpState) {
        unsigned long remSec = (pumpStopAtMs > millis()) ? ((pumpStopAtMs - millis() + 999) / 1000) : 0;
        display.printf("BOM: [ DANG BAT %lus ]", remSec);
    } else {
        display.print("BOM: [ DANG TAT ]");
    }

    display.display();
}

void ensureNetworkAndMqtt() {
    if (WiFi.status() != WL_CONNECTED) {
        unsigned long nowMs = millis();
        if (nowMs - lastReconnectAttemptMs >= 5000) {
            lastReconnectAttemptMs = nowMs;
            Serial.printf("[WIFI] Dang ket noi toi SSID: %s ...\n", WIFI_SSID);
            WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
        }
        return;
    }

    if (!isTimeSynced()) {
        return;
    }

    if (!mqttClient.connected()) {
        unsigned long nowMs = millis();
        if (nowMs - lastReconnectAttemptMs >= 3000) {
            lastReconnectAttemptMs = nowMs;
            String clientId = String("esp32-hw-") + bootId.substring(0, 8);

            JsonDocument willDoc;
            willDoc["schema_version"] = 1;
            willDoc["device_id"]      = DEVICE_ID;
            willDoc["source"]         = SOURCE_NAME;
            willDoc["timestamp"]      = getIsoTimestamp();
            willDoc["online"]         = false;
            willDoc["pump"]           = "unknown";
            willDoc["scenario"]       = "normal";
            char willBuf[320];
            serializeJson(willDoc, willBuf, sizeof(willBuf));

            Serial.printf("[MQTT] Dang ket noi Broker %s:%d ...\n", MQTT_HOST, MQTT_PORT);
            if (mqttClient.connect(clientId.c_str(), nullptr, nullptr,
                                   TOPIC_STATUS, 1, true, willBuf)) {
                Serial.println("[MQTT] Ket noi thanh cong! Dang ky nhan lenh command...");
                mqttClient.subscribe(TOPIC_COMMAND, 1);
                publishStatus(true);
                sampleAndPublishTelemetry();
                lastTelemetryMs = millis();
            } else {
                Serial.printf("[MQTT] Ket noi that bai (rc=%d)\n", mqttClient.state());
            }
        }
    }
}

void setup() {
    // 1. Chốt an toàn chân Relay OFF ngay lập tức khi cấp nguồn
    digitalWrite(PUMP_PIN, RELAY_OFF);
    pinMode(PUMP_PIN, OUTPUT);
    digitalWrite(PUMP_PIN, RELAY_OFF);

    pinMode(LED_PIN, OUTPUT);
    digitalWrite(LED_PIN, LOW);

    Serial.begin(115200);
    delay(500);
    Serial.println("\n================================================");
    Serial.println("  AIoT PLANT CARE - ESP32 MQTT LIVE FIRMWARE");
    Serial.println("================================================");

    bootId = generateUuidV4();
    Serial.printf("[BOOT] Boot ID: %s\n", bootId.c_str());

    // 2. Khởi tạo I2C & OLED
    Wire.begin(I2C_SDA, I2C_SCL);
    Wire.setTimeOut(30);

    oledAvailable = display.begin(SSD1306_SWITCHCAPVCC, 0x3C);
    if (oledAvailable) {
        Serial.println("[OLED] Da tim thay man hinh OLED SSD1306 tai dia chi 0x3C");
        display.clearDisplay();
        display.setTextSize(1);
        display.setTextColor(SSD1306_WHITE);
        display.setCursor(8, 16);
        display.println("AIoT PLANT CARE");
        display.setCursor(8, 32);
        display.println("Ket noi Wi-Fi & NTP...");
        display.display();
    } else {
        Serial.println("[OLED] Khong phat hien OLED SSD1306 (bo qua hien thi)");
    }

    // 3. Khởi tạo BMP180 & DHT22
    bmpAvailable = bmp.begin();
    if (bmpAvailable) {
        Serial.printf("[BMP] Khoi tao BMP180 thanh cong! Ap suat: %.1f hPa\n",
                      bmp.readPressure() / 100.0f);
    } else {
        Serial.println("[BMP] Chua phat hien BMP180 tai dia chi 0x77.");
    }
    dht.begin();

    // 4. Kết nối Wi-Fi & cấu hình NTP
    WiFi.mode(WIFI_STA);
    WiFi.setAutoReconnect(true);
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
    Serial.printf("[WIFI] Dang ket noi Wi-Fi: %s", WIFI_SSID);

    unsigned long wifiStart = millis();
    while (WiFi.status() != WL_CONNECTED && (millis() - wifiStart < 15000)) {
        delay(300);
        Serial.print(".");
    }
    Serial.println();

    if (WiFi.status() == WL_CONNECTED) {
        Serial.printf("[WIFI] Da ket noi! IP ESP32: %s\n", WiFi.localIP().toString().c_str());
    }

    configTime(0, 0, "pool.ntp.org", "time.nist.gov");
    Serial.print("[NTP] Dang dong bo gio UTC");
    unsigned long ntpStart = millis();
    while (!isTimeSynced() && (millis() - ntpStart < 10000)) {
        delay(300);
        Serial.print(".");
    }
    Serial.println();
    if (ntpSynced) {
        Serial.printf("[NTP] Gio UTC hien tai: %s\n", getIsoTimestamp().c_str());
    }

    // 5. Cấu hình MQTT
    mqttClient.setServer(MQTT_HOST, MQTT_PORT);
    mqttClient.setBufferSize(1024);
    mqttClient.setKeepAlive(20);
    mqttClient.setCallback(onMqttMessage);
}

void loop() {
    unsigned long nowMs = millis();

    // 1. Kiểm tra bộ đếm phần cứng tự động ngắt bơm (Fail-safe non-blocking timer)
    if (pumpState && nowMs >= pumpStopAtMs) {
        String finishedCmd = activeCommandId;
        activeCommandId = "";
        pumpStopAtMs = 0;
        setPumpHardware(false);
        Serial.println(">> [BOM] Het thoi luong tuoi -> Tu dong NGAT BOM!");
        publishStatus(true, "off");
        if (finishedCmd.length() > 0) {
            publishAck(finishedCmd, "completed", "Đã hoàn tất tưới theo thời lượng");
        }
    }

    // 2. Duy trì kết nối mạng & MQTT
    ensureNetworkAndMqtt();
    if (mqttClient.connected()) {
        mqttClient.loop();
    }

    // 3. Đọc cảm biến & gửi Telemetry mỗi 5 giây
    if (nowMs - lastTelemetryMs >= 5000) {
        lastTelemetryMs = nowMs;

        // Ping kiểm tra địa chỉ I2C 0x77 (BMP180) và 0x76 (BMP280)
        int sdaVal = digitalRead(I2C_SDA);
        int sclVal = digitalRead(I2C_SCL);
        Wire.beginTransmission(0x77);
        byte err77 = Wire.endTransmission();
        Wire.beginTransmission(0x76);
        byte err76 = Wire.endTransmission();

        Serial.printf("[I2C DIAG] SDA(21)=%d SCL(22)=%d | Ping 0x77 (BMP180): %d | Ping 0x76 (BMP280): %d\n",
                      sdaVal, sclVal, err77, err76);

        if (err77 == 5 || err76 == 5) {
            // Bus I2C bị kẹt (timeout), reset lại bộ điều khiển I2C
            Wire.end();
            Wire.begin(I2C_SDA, I2C_SCL);
            Wire.setTimeOut(30);
        }

        if (!bmpAvailable && bmp.begin()) {
            bmpAvailable = true;
            Serial.println("[BMP] >> Da tu dong phat hien va ket noi BMP180 thanh cong!");
        }
        sampleAndPublishTelemetry();
    }

    // 4. Cập nhật màn hình OLED mỗi 500ms
    if (nowMs - lastDisplayMs >= 500) {
        lastDisplayMs = nowMs;
        renderDashboard();
    }
}
