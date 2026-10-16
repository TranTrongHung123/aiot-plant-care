#include <Arduino.h>
#include "esp_camera.h"

void setup() {
    Serial.begin(115200);
    delay(1000);

    Serial.println("ESP32-CAM started successfully!");
}

void loop() {
    delay(1000);
}