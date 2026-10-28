#include <Arduino.h>
#include <WiFi.h>
#include "esp_camera.h"
#include "esp_http_server.h"
#include "soc/soc.h"
#include "soc/rtc_cntl_reg.h"

#if __has_include("secrets.h")
#include "secrets.h"
#else
#include "secrets.example.h"
#endif

// --- SƠ ĐỒ CHÂN CHUẨN AI-THINKER ESP32-CAM (OV2640) ---
#define PWDN_GPIO_NUM     32
#define RESET_GPIO_NUM    -1
#define XCLK_GPIO_NUM      0
#define SIOD_GPIO_NUM     26
#define SIOC_GPIO_NUM     27

#define Y9_GPIO_NUM       35
#define Y8_GPIO_NUM       34
#define Y7_GPIO_NUM       39
#define Y6_GPIO_NUM       36
#define Y5_GPIO_NUM       21
#define Y4_GPIO_NUM       19
#define Y3_GPIO_NUM       18
#define Y2_GPIO_NUM        5
#define VSYNC_GPIO_NUM    25
#define HREF_GPIO_NUM     23
#define PCLK_GPIO_NUM     22

#define FLASH_LED_PIN      4   // Đèn Flash trắng (Mặc định tắt: LOW)
#define STATUS_LED_PIN    33   // Đèn LED đỏ mặt sau (Active-LOW)

// Chu kỳ chụp tự động: 30 phút = 1.800.000 ms
static const unsigned long CAPTURE_INTERVAL_MS = 30UL * 60UL * 1000UL;

// Trạng thái vận hành 2 chế độ
static SemaphoreHandle_t camMutex = nullptr;
static volatile bool streamEnabled = false;
static volatile bool captureRequested = false;
static volatile bool captureInProgress = false;
static volatile bool isStreamingClientConnected = false;

static unsigned long lastCaptureMs = 0;
static bool initialCaptureDone = false;
static uint32_t uploadCount = 0;
static bool lastUploadOk = false;
static int lastLeafStatus = 0;
static int lastFruitStatus = 0;

static httpd_handle_t ctrlHttpd = nullptr;
static httpd_handle_t streamHttpd = nullptr;

static const char* STREAM_CONTENT_TYPE = "multipart/x-mixed-replace;boundary=frame";
static const char* STREAM_BOUNDARY     = "\r\n--frame\r\n";
static const char* STREAM_PART         = "Content-Type: image/jpeg\r\nContent-Length: %u\r\n\r\n";

static void setCorsHeaders(httpd_req_t* req) {
    httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
    httpd_resp_set_hdr(req, "Access-Control-Allow-Methods", "GET, POST, OPTIONS");
    httpd_resp_set_hdr(req, "Access-Control-Allow-Headers", "Content-Type, Authorization");
}

static unsigned long getNextCaptureSeconds() {
    unsigned long nowMs = millis();
    unsigned long elapsed = nowMs - lastCaptureMs;
    if (elapsed >= CAPTURE_INTERVAL_MS) {
        return 0;
    }
    return (CAPTURE_INTERVAL_MS - elapsed) / 1000UL;
}

static void buildStatusJson(char* outBuf, size_t outSize) {
    String ipStr = WiFi.localIP().toString();
    snprintf(outBuf, outSize,
             "{\"online\":true,\"ip\":\"%s\",\"stream_enabled\":%s,"
             "\"stream_url\":\"http://%s:8081/stream\","
             "\"interval_seconds\":%lu,\"next_capture_in_seconds\":%lu,"
             "\"upload_count\":%u,\"last_upload_ok\":%s,"
             "\"last_leaf_status\":%d,\"last_fruit_status\":%d,"
             "\"capture_in_progress\":%s}",
             ipStr.c_str(),
             streamEnabled ? "true" : "false",
             ipStr.c_str(),
             CAPTURE_INTERVAL_MS / 1000UL,
             getNextCaptureSeconds(),
             (unsigned int)uploadCount,
             lastUploadOk ? "true" : "false",
             lastLeafStatus,
             lastFruitStatus,
             captureInProgress ? "true" : "false");
}

// Gửi 1 khung ảnh JPEG lên Backend qua HTTP POST multipart/form-data
static int uploadFrameToBackend(const uint8_t* jpgBuf, size_t jpgLen, const char* purpose) {
    WiFiClient client;
    client.setTimeout(5000);
    if (!client.connect(BACKEND_HOST, BACKEND_PORT, 4000)) {
        Serial.printf("[UPLOAD] Khong ket noi duoc Backend %s:%d (purpose=%s)\n",
                      BACKEND_HOST, BACKEND_PORT, purpose);
        return -1;
    }

    String boundary = "----ESP32CamBoundary7MA4YWxkTrZu0gW";
    String head = "--" + boundary + "\r\n"
                  "Content-Disposition: form-data; name=\"file\"; filename=\"esp32cam-" + String(purpose) + ".jpg\"\r\n"
                  "Content-Type: image/jpeg\r\n\r\n";
    String tail = "\r\n--" + boundary + "--\r\n";
    size_t totalLen = head.length() + jpgLen + tail.length();

    client.printf("POST /api/v1/images?purpose=%s HTTP/1.1\r\n", purpose);
    client.printf("Host: %s:%d\r\n", BACKEND_HOST, BACKEND_PORT);
    client.printf("Authorization: Bearer %s\r\n", APP_TOKEN);
    client.printf("Content-Type: multipart/form-data; boundary=%s\r\n", boundary.c_str());
    client.printf("Content-Length: %u\r\n", (unsigned int)totalLen);
    client.print("Connection: close\r\n\r\n");

    client.print(head);
    size_t offset = 0;
    const size_t chunkSize = 1436; // Khớp 1 TCP MSS tránh phân mảnh và nghẽn buffer lwIP
    unsigned long writeStart = millis();
    while (offset < jpgLen && client.connected()) {
        size_t toWrite = (jpgLen - offset > chunkSize) ? chunkSize : (jpgLen - offset);
        size_t written = client.write(jpgBuf + offset, toWrite);
        if (written > 0) {
            offset += written;
            writeStart = millis();
        } else {
            if (millis() - writeStart > 4000) {
                Serial.printf("[UPLOAD] Timeout ghi TCP body (%u/%u bytes)\n",
                              (unsigned int)offset, (unsigned int)jpgLen);
                client.stop();
                return -1;
            }
            delay(5);
        }
    }
    client.print(tail);

    int statusCode = -1;
    unsigned long waitStart = millis();
    while (client.connected() && (millis() - waitStart < 6000)) {
        if (client.available()) {
            String statusLine = client.readStringUntil('\n');
            statusLine.trim();
            if (statusLine.startsWith("HTTP/1.")) {
                int firstSpace = statusLine.indexOf(' ');
                if (firstSpace > 0) {
                    statusCode = statusLine.substring(firstSpace + 1, firstSpace + 4).toInt();
                }
                break;
            }
        }
        delay(10);
    }
    // Đọc xả nhanh phần còn lại để đóng TCP sạch (FIN thay vì RST)
    unsigned long drainStart = millis();
    while (client.available() && (millis() - drainStart < 100)) {
        client.read();
    }
    client.stop();
    Serial.printf("[UPLOAD] POST /api/v1/images?purpose=%s (%u bytes) -> HTTP %d\n",
                  purpose, (unsigned int)jpgLen, statusCode);
    return statusCode;
}

// Chụp khung hình mới nhất và gửi cho CẢ 2 tác vụ AI (leaf + fruit)
static bool captureAndUploadBothTasks() {
    lastCaptureMs = millis(); // Luôn cập nhật timestamp ngay lập tức để không bị lặp vô hạn khi lỗi

    if (WiFi.status() != WL_CONNECTED) {
        Serial.println("[CAPTURE] Bo qua vi Wi-Fi chua ket noi.");
        return false;
    }

    captureInProgress = true;
    digitalWrite(STATUS_LED_PIN, LOW); // Bật LED đỏ báo đang chụp

    if (xSemaphoreTake(camMutex, pdMS_TO_TICKS(5000)) != pdTRUE) {
        Serial.println("[CAPTURE] Khong lay duoc quyen truy cap camera (mutex timeout).");
        digitalWrite(STATUS_LED_PIN, HIGH);
        captureInProgress = false;
        return false;
    }

    camera_fb_t* fb = esp_camera_fb_get();
    if (!fb || !fb->buf || fb->len == 0) {
        if (fb) esp_camera_fb_return(fb);
        delay(100);
        fb = esp_camera_fb_get();
    }

    if (!fb || !fb->buf || fb->len == 0) {
        Serial.println("[CAPTURE] Loi chup khung hinh tu OV2640!");
        if (fb) {
            esp_camera_fb_return(fb);
        }
        xSemaphoreGive(camMutex);
        digitalWrite(STATUS_LED_PIN, HIGH);
        captureInProgress = false;
        return false;
    }

    Serial.printf("[CAPTURE] Da chup khung hinh JPEG (%u bytes). Dang gui 2 tac vu AI (leaf + fruit)...\n",
                  (unsigned int)fb->len);

    // Giải phóng camMutex ngay lập tức bằng buffer tạm để luồng Livestream không bị khựng/timeout
    uint8_t* uploadBuf = psramFound() ? (uint8_t*)ps_malloc(fb->len) : (uint8_t*)malloc(fb->len);
    int codeLeaf  = -1;
    int codeFruit = -1;

    if (uploadBuf) {
        memcpy(uploadBuf, fb->buf, fb->len);
        size_t uploadLen = fb->len;
        esp_camera_fb_return(fb);
        xSemaphoreGive(camMutex);

        codeLeaf  = uploadFrameToBackend(uploadBuf, uploadLen, "leaf");
        codeFruit = uploadFrameToBackend(uploadBuf, uploadLen, "fruit");
        free(uploadBuf);
    } else {
        codeLeaf  = uploadFrameToBackend(fb->buf, fb->len, "leaf");
        codeFruit = uploadFrameToBackend(fb->buf, fb->len, "fruit");
        esp_camera_fb_return(fb);
        xSemaphoreGive(camMutex);
    }

    lastLeafStatus  = codeLeaf;
    lastFruitStatus = codeFruit;
    bool ok = (codeLeaf >= 200 && codeLeaf < 300) && (codeFruit >= 200 && codeFruit < 300);
    lastUploadOk = ok;
    if (ok) {
        uploadCount++;
    }
    lastCaptureMs = millis();
    digitalWrite(STATUS_LED_PIN, HIGH);
    captureInProgress = false;

    Serial.printf("[CAPTURE] Ket qua gui kep: leaf=%d, fruit=%d -> %s (tong thanh cong: %u)\n",
                  codeLeaf, codeFruit, ok ? "OK" : "FAILED", (unsigned int)uploadCount);
    return ok;
}

// --- CÁC HANDLER HTTP ĐIỀU KHIỂN (CỔNG 80) ---

static esp_err_t optionsHandler(httpd_req_t* req) {
    setCorsHeaders(req);
    httpd_resp_set_hdr(req, "Connection", "close");
    httpd_resp_set_status(req, "204 No Content");
    return httpd_resp_send(req, nullptr, 0);
}

static esp_err_t statusHandler(httpd_req_t* req) {
    setCorsHeaders(req);
    httpd_resp_set_hdr(req, "Connection", "close");
    httpd_resp_set_type(req, "application/json");
    char json[512];
    buildStatusJson(json, sizeof(json));
    return httpd_resp_send(req, json, strlen(json));
}

static esp_err_t streamOnHandler(httpd_req_t* req) {
    setCorsHeaders(req);
    httpd_resp_set_hdr(req, "Connection", "close");
    streamEnabled = true;
    Serial.println(">> [MODE] LIVESTREAM = ON (Mo luong :8081/stream)");
    httpd_resp_set_type(req, "application/json");
    char json[512];
    buildStatusJson(json, sizeof(json));
    return httpd_resp_send(req, json, strlen(json));
}

static esp_err_t streamOffHandler(httpd_req_t* req) {
    setCorsHeaders(req);
    httpd_resp_set_hdr(req, "Connection", "close");
    streamEnabled = false;
    Serial.println(">> [MODE] LIVESTREAM = OFF (Ngat luong de giu mat camera)");
    httpd_resp_set_type(req, "application/json");
    char json[512];
    buildStatusJson(json, sizeof(json));
    return httpd_resp_send(req, json, strlen(json));
}

static esp_err_t captureNowHandler(httpd_req_t* req) {
    setCorsHeaders(req);
    httpd_resp_set_hdr(req, "Connection", "close");
    Serial.println(">> [CMD] Nhan yeu cau chup & phan tich kep (leaf + fruit) ngay lap tuc!");
    captureRequested = true;

    // Chỉ đợi tối đa 250ms để loop() bắt đầu chụp rồi trả phản hồi ngay, không giữ socket HTTP lâu
    unsigned long waitStart = millis();
    while (captureRequested && (millis() - waitStart < 250)) {
        delay(20);
    }

    httpd_resp_set_type(req, "application/json");
    char json[512];
    buildStatusJson(json, sizeof(json));
    return httpd_resp_send(req, json, strlen(json));
}

static esp_err_t indexHandler(httpd_req_t* req) {
    setCorsHeaders(req);
    httpd_resp_set_hdr(req, "Connection", "close");
    httpd_resp_set_type(req, "text/html; charset=utf-8");
    const char* html =
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>AIoT Plant Care • ESP32-CAM</title>"
        "<style>body{font-family:system-ui,sans-serif;max-width:680px;margin:24px auto;padding:0 16px;background:#0f172a;color:#f8fafc}"
        ".card{background:#1e293b;padding:20px;border-radius:12px;margin-bottom:16px}"
        "button{padding:10px 16px;border-radius:8px;border:none;font-weight:600;cursor:pointer;margin-right:8px}"
        ".on{background:#22c55e;color:#052e16}.off{background:#ef4444;color:#fff}.cap{background:#38bdf8;color:#082f49}"
        "img{width:100%;border-radius:8px;margin-top:12px;background:#000}</style></head><body>"
        "<h2>AIoT Plant Care · ESP32-CAM</h2>"
        "<div class='card'><p id='st'>Đang tải trạng thái...</p>"
        "<button class='on' onclick=\"act('/stream/on')\">Bật Livestream</button>"
        "<button class='off' onclick=\"act('/stream/off')\">Tắt Livestream</button>"
        "<button class='cap' onclick=\"act('/capture-now')\">Chụp gửi AI (Lá + Quả)</button>"
        "<button onclick=\"refresh()\" style='background:#475569;color:#fff'>Làm mới</button>"
        "<p style='margin-top:14px;color:#38bdf8;font-size:0.9rem'>💡 Livestream được phân phối qua Backend WebSocket Relay để tối ưu hiệu năng và gắn nhãn AI thời gian thực.</p>"
        "<p><a href='http://" BACKEND_HOST ":3000/ai' style='color:#38bdf8;font-weight:600;text-decoration:underline'>👉 Mở Dashboard Web Frontend (/ai)</a></p></div>"
        "<script>async function refresh(){const r=await fetch('/status');const d=await r.json();"
        "document.getElementById('st').innerText=`Livestream: ${d.stream_enabled?'ĐANG BẬT':'ĐANG TẮT'} | Chụp kế tiếp: ${Math.floor(d.next_capture_in_seconds/60)}p ${d.next_capture_in_seconds%60}s | Đã gửi AI: ${d.upload_count} lần`;}"
        "async function act(p){await fetch(p,{method:'POST'});setTimeout(refresh,500);}"
        "refresh();</script></body></html>";
    return httpd_resp_send(req, html, strlen(html));
}

// --- HANDLER LUỒNG VIDEO MJPEG (CỔNG 81) ---

static esp_err_t streamHandler(httpd_req_t* req) {
    if (!streamEnabled) {
        setCorsHeaders(req);
        httpd_resp_set_status(req, "409 Conflict");
        httpd_resp_set_hdr(req, "Connection", "close");
        httpd_resp_set_type(req, "application/json");
        const char* msg = "{\"detail\":\"Livestream đang ở trạng thái TẮT. Hãy bật Livestream trước.\"}";
        return httpd_resp_send(req, msg, strlen(msg));
    }

    httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
    esp_err_t res = httpd_resp_set_type(req, STREAM_CONTENT_TYPE);
    if (res != ESP_OK) {
        return res;
    }

    char partBuf[64];
    Serial.println("[STREAM] Client bat dau xem MJPEG stream...");

    while (streamEnabled) {
        if (xSemaphoreTake(camMutex, pdMS_TO_TICKS(100)) != pdTRUE) {
            delay(10);
            continue;
        }
        camera_fb_t* fb = esp_camera_fb_get();
        if (!fb) {
            xSemaphoreGive(camMutex);
            delay(20);
            continue;
        }

        res = httpd_resp_send_chunk(req, STREAM_BOUNDARY, strlen(STREAM_BOUNDARY));
        if (res == ESP_OK) {
            size_t hlen = snprintf(partBuf, sizeof(partBuf), STREAM_PART, (unsigned int)fb->len);
            res = httpd_resp_send_chunk(req, partBuf, hlen);
        }
        if (res == ESP_OK) {
            res = httpd_resp_send_chunk(req, (const char*)fb->buf, fb->len);
        }

        esp_camera_fb_return(fb);
        xSemaphoreGive(camMutex);

        if (res != ESP_OK) {
            Serial.printf("[STREAM] Send error: %d, ngat ket noi.\n", res);
            break;
        }
        delay(5); // Nghỉ 5ms để nhường CPU nhẹ cho Wi-Fi, đạt tốc độ khung hình ~20-22 FPS mượt mà
    }

    Serial.println("[STREAM] Ket thuc phien xem MJPEG stream.");
    return ESP_OK;
}

static void startCameraServers() {
    httpd_config_t config = HTTPD_DEFAULT_CONFIG();
    config.server_port = 80;
    config.ctrl_port = 32768;
    config.max_uri_handlers = 12;
    config.max_open_sockets = 4;
    config.lru_purge_enable = true;
    config.recv_wait_timeout = 5;
    config.send_wait_timeout = 5;
    config.stack_size = 8192;

    if (httpd_start(&ctrlHttpd, &config) == ESP_OK) {
        httpd_uri_t indexUri      = { .uri = "/",            .method = HTTP_GET,     .handler = indexHandler,      .user_ctx = nullptr };
        httpd_uri_t statusUri     = { .uri = "/status",      .method = HTTP_GET,     .handler = statusHandler,     .user_ctx = nullptr };
        httpd_uri_t streamOnUri   = { .uri = "/stream/on",   .method = HTTP_POST,    .handler = streamOnHandler,   .user_ctx = nullptr };
        httpd_uri_t streamOffUri  = { .uri = "/stream/off",  .method = HTTP_POST,    .handler = streamOffHandler,  .user_ctx = nullptr };
        httpd_uri_t captureNowUri = { .uri = "/capture-now", .method = HTTP_POST,    .handler = captureNowHandler, .user_ctx = nullptr };

        httpd_uri_t optStatusUri  = { .uri = "/status",      .method = HTTP_OPTIONS, .handler = optionsHandler,    .user_ctx = nullptr };
        httpd_uri_t optOnUri      = { .uri = "/stream/on",   .method = HTTP_OPTIONS, .handler = optionsHandler,    .user_ctx = nullptr };
        httpd_uri_t optOffUri     = { .uri = "/stream/off",  .method = HTTP_OPTIONS, .handler = optionsHandler,    .user_ctx = nullptr };
        httpd_uri_t optCapUri     = { .uri = "/capture-now", .method = HTTP_OPTIONS, .handler = optionsHandler,    .user_ctx = nullptr };

        httpd_register_uri_handler(ctrlHttpd, &indexUri);
        httpd_register_uri_handler(ctrlHttpd, &statusUri);
        httpd_register_uri_handler(ctrlHttpd, &streamOnUri);
        httpd_register_uri_handler(ctrlHttpd, &streamOffUri);
        httpd_register_uri_handler(ctrlHttpd, &captureNowUri);
        httpd_register_uri_handler(ctrlHttpd, &optStatusUri);
        httpd_register_uri_handler(ctrlHttpd, &optOnUri);
        httpd_register_uri_handler(ctrlHttpd, &optOffUri);
        httpd_register_uri_handler(ctrlHttpd, &optCapUri);
        Serial.println("[HTTP] Control Server da mo tai cong 80.");
    }

    httpd_config_t streamConfig = HTTPD_DEFAULT_CONFIG();
    streamConfig.server_port = 8081;
    streamConfig.ctrl_port = 32769;
    streamConfig.stack_size = 8192;
    streamConfig.max_open_sockets = 4;     // Cho phép 4 socket đồng thời để giải phóng các phiên cũ nhanh chóng
    streamConfig.lru_purge_enable = true;  // Tự động giải phóng socket cũ nếu client kết nối lại
    streamConfig.recv_wait_timeout = 5;
    streamConfig.send_wait_timeout = 5;

    if (httpd_start(&streamHttpd, &streamConfig) == ESP_OK) {
        httpd_uri_t streamUri = { .uri = "/stream", .method = HTTP_GET, .handler = streamHandler, .user_ctx = nullptr };
        httpd_register_uri_handler(streamHttpd, &streamUri);
        Serial.println("[HTTP] Stream Server da mo tai cong 8081 (/stream).");
    }
}

static bool initCamera() {
    camera_config_t config;
    config.ledc_channel = LEDC_CHANNEL_0;
    config.ledc_timer   = LEDC_TIMER_0;
    config.pin_d0       = Y2_GPIO_NUM;
    config.pin_d1       = Y3_GPIO_NUM;
    config.pin_d2       = Y4_GPIO_NUM;
    config.pin_d3       = Y5_GPIO_NUM;
    config.pin_d4       = Y6_GPIO_NUM;
    config.pin_d5       = Y7_GPIO_NUM;
    config.pin_d6       = Y8_GPIO_NUM;
    config.pin_d7       = Y9_GPIO_NUM;
    config.pin_xclk     = XCLK_GPIO_NUM;
    config.pin_pclk     = PCLK_GPIO_NUM;
    config.pin_vsync    = VSYNC_GPIO_NUM;
    config.pin_href     = HREF_GPIO_NUM;
    config.pin_sccb_sda = SIOD_GPIO_NUM;
    config.pin_sccb_scl = SIOC_GPIO_NUM;
    config.pin_pwdn     = PWDN_GPIO_NUM;
    config.pin_reset    = RESET_GPIO_NUM;
    config.xclk_freq_hz = 20000000;
    config.pixel_format = PIXFORMAT_JPEG;

    if (psramFound()) {
        config.frame_size   = FRAMESIZE_VGA;  // 640x480 tối ưu mượt mà cho stream & khớp kích thước YOLO
        config.jpeg_quality = 18;             // Nén nhẹ (~12KB/frame) giảm nhiệt độ chip và độ trễ Wi-Fi
        config.fb_count     = 2;
        config.grab_mode    = CAMERA_GRAB_LATEST;
    } else {
        config.frame_size   = FRAMESIZE_VGA;  // 640x480
        config.jpeg_quality = 18;
        config.fb_count     = 1;
        config.grab_mode    = CAMERA_GRAB_WHEN_EMPTY;
    }

    esp_err_t err = esp_camera_init(&config);
    if (err != ESP_OK) {
        Serial.printf("[CAM ERR] Khoi tao camera that bai (0x%x)\n", err);
        return false;
    }

    sensor_t* s = esp_camera_sensor_get();
    if (s) {
        s->set_brightness(s, 0);
        s->set_contrast(s, 0);
        s->set_saturation(s, 0);
    }
    Serial.println("[CAM] Khoi tao OV2640 thanh cong!");
    return true;
}

void setup() {
    // Tắt detector sụt áp quá nhạy khi khởi động Wi-Fi trên ESP32-CAM
    WRITE_PERI_REG(RTC_CNTL_BROWN_OUT_REG, 0);

    pinMode(FLASH_LED_PIN, OUTPUT);
    digitalWrite(FLASH_LED_PIN, LOW); // Đảm bảo đèn Flash tắt

    pinMode(STATUS_LED_PIN, OUTPUT);
    digitalWrite(STATUS_LED_PIN, HIGH); // Tắt đèn LED đỏ (Active-LOW)

    Serial.begin(115200);
    delay(500);
    Serial.println("\n====================================================");
    Serial.println("  AIoT PLANT CARE - ESP32-CAM DUAL MODE FIRMWARE");
    Serial.println("====================================================");

    camMutex = xSemaphoreCreateMutex();

    if (!initCamera()) {
        Serial.println("[FATAL] Kiem tra lai cap ruy-bang camera OV2640!");
    }

    WiFi.mode(WIFI_STA);
    WiFi.setSleep(false); // Tăng tốc độ phản hồi HTTP & Stream
    WiFi.setAutoReconnect(true);
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
    Serial.printf("[WIFI] Dang ket noi Wi-Fi: %s", WIFI_SSID);

    unsigned long startWait = millis();
    while (WiFi.status() != WL_CONNECTED && (millis() - startWait < 20000)) {
        delay(300);
        Serial.print(".");
    }
    Serial.println();

    if (WiFi.status() == WL_CONNECTED) {
        Serial.printf("[WIFI] Da ket noi! IP ESP32-CAM: %s\n", WiFi.localIP().toString().c_str());
        Serial.printf("[INFO] Trang dieu khien: http://%s/\n", WiFi.localIP().toString().c_str());
        Serial.printf("[INFO] Luong Livestream: http://%s:8081/stream (Mac dinh: OFF)\n", WiFi.localIP().toString().c_str());
    }

    startCameraServers();
    lastCaptureMs = millis();
}

void loop() {
    unsigned long nowMs = millis();

    // Tự động kết nối lại Wi-Fi nếu rớt mạng
    static unsigned long lastWifiCheckMs = 0;
    if (WiFi.status() != WL_CONNECTED && (nowMs - lastWifiCheckMs >= 5000)) {
        lastWifiCheckMs = nowMs;
        Serial.println("[WIFI] Mat ket noi, dang thu ket noi lai...");
        WiFi.reconnect();
    }

    // Chụp lần đầu sau khi khởi động 8 giây để có ngay dữ liệu AI nếu chưa bật livestream, sau đó cứ mỗi 30 phút chụp 1 lần
    if (!initialCaptureDone && WiFi.status() == WL_CONNECTED && nowMs >= 8000) {
        initialCaptureDone = true;
        if (!streamEnabled) {
            Serial.println(">> [AUTO] Chup anh khoi dong lan dau gui Backend AI (leaf + fruit)...");
            captureAndUploadBothTasks();
        }
    }

    // Nếu Livestream đang BẬT: Backend tự trích xuất snapshot trực tiếp từ stream
    // Chỉ upload HTTP định kỳ khi Livestream đang TẮT hoặc khi có lệnh bấm chụp thủ công
    if (captureRequested || (!streamEnabled && (nowMs - lastCaptureMs >= CAPTURE_INTERVAL_MS))) {
        captureRequested = false;
        Serial.println(">> [SCHEDULE/CMD] Thuc hien chup anh & gui 2 tac vu AI (leaf + fruit)...");
        captureAndUploadBothTasks();
    }

    delay(50);
}