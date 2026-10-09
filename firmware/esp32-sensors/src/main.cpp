#include <Arduino.h>

void setup() {
    Serial.begin(115200);
    delay(1000);

    Serial.println("ESP32 started successfully!");
}

void loop() {
    Serial.println("Hello from ESP32!");
    delay(1000);
}
