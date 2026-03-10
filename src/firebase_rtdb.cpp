// ═══════════════════════════════════════════════════════════════
//  FIREBASE RTDB — sends raw + processed sensor data via REST
//  Endpoint: PATCH /s01.json  (merges into existing data)
// ═══════════════════════════════════════════════════════════════

#include <Arduino.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <esp_wifi.h>

#include "firebase_config.h"
#include "firebase_rtdb.h"
#include "flex_sensor.h"
#include "mpu6050.h"
#include "gsr.h"
#include "max30102.h"

extern float              g_tempC;
extern SemaphoreHandle_t  i2cMutex;

// ═══════════════════════════════════════════════════════════════
//  WiFi — no longer called from setup(); firebaseTask owns WiFi
// ═══════════════════════════════════════════════════════════════
void wifiConnect() {
  // Intentionally empty — WiFi is now managed inside firebaseTask
}

// ═══════════════════════════════════════════════════════════════
//  Firebase RTDB Task
// ═══════════════════════════════════════════════════════════════
void firebaseTask(void *pv) {
  // Let sensor tasks stabilise before touching WiFi
  Serial.println("INFO:Firebase task waiting 5s for sensors to stabilise...");
  vTaskDelay(pdMS_TO_TICKS(5000));

  // ── Start WiFi with lowest practical power ────────────────
  Serial.printf("INFO:Heap before WiFi: %lu\n", (unsigned long)ESP.getFreeHeap());
  WiFi.mode(WIFI_STA);
  WiFi.setTxPower(WIFI_POWER_8_5dBm);
  esp_wifi_set_ps(WIFI_PS_MAX_MODEM);   // max power saving — radio sleeps between beacons
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.println("INFO:WiFi started");
  Serial.printf("INFO:Heap after WiFi.begin: %lu\n", (unsigned long)ESP.getFreeHeap());

  // Wait for connection (up to 20s)
  int attempts = 0;
  while (WiFi.status() != WL_CONNECTED && attempts < 40) {
    vTaskDelay(pdMS_TO_TICKS(500));
    attempts++;
  }

  if (WiFi.status() == WL_CONNECTED) {
    Serial.print("INFO:WiFi connected, IP=");
    Serial.println(WiFi.localIP());
  } else {
    Serial.println("WARN:WiFi failed — will keep retrying");
  }

  WiFiClientSecure sslClient;
  sslClient.setInsecure();  // skip cert check (add setCACert for production)

  for (;;) {
    // ── Wait for WiFi ──────────────────────────────────────────
    if (WiFi.status() != WL_CONNECTED) {
      vTaskDelay(pdMS_TO_TICKS(5000));
      continue;
    }

    // ── Snapshot all sensor globals ────────────────────────────
    int  fRaw[5], fKal[5];
    int  gsrRawLocal;
    uint8_t gestureLocal;

    portENTER_CRITICAL(&g_flexMux);
    for (int i = 0; i < 5; i++) {
      fRaw[i] = flexRaw[i];
      fKal[i] = flexKal[i];
    }
    gsrRawLocal  = g_gsrRaw;
    gestureLocal = g_stableGesture;
    portEXIT_CRITICAL(&g_flexMux);

    float aX = accX, aY = accY, aZ = accZ;
    float gX = gyX,  gY = gyY,  gZ = gyZ;
    float r  = roll, p  = pitch, y = yaw;

    int      bpm   = outBPM;
    long     ir    = outIR;
    int32_t  spo2  = lastValidSPO2;
    uint32_t red   = lastRedV;
    float    tempC = g_tempC;
    HandPos  hp    = g_handPos;

    float gsrKalVal = gsrKF.x;
    float vGSR      = gsrRawLocal * (3.3f / 4095.0f);

    const char* handStr    = (hp == HAND_UP) ? "UP"
                           : (hp == HAND_DOWN) ? "DOWN" : "REST";
    const char* gestureTxt = gestureToText(gestureLocal);

    // ── Build JSON payload ─────────────────────────────────────
    char json[1024];
    snprintf(json, sizeof(json),
      "{"
        "\"raw\":{"
          "\"accel\":{\"x\":%.3f,\"y\":%.3f,\"z\":%.3f},"
          "\"gyro\":{\"x\":%.3f,\"y\":%.3f,\"z\":%.3f},"
          "\"max30102\":{\"ir\":%ld,\"red\":%lu},"
          "\"gsr\":%d,"
          "\"flex\":{\"ring\":%d,\"thumb\":%d,\"middle\":%d,\"pinky\":%d,\"index\":%d},"
          "\"skin_temp_c\":%.2f"
        "},"
        "\"processed\":{"
          "\"imu\":{\"roll\":%.2f,\"pitch\":%.2f,\"yaw\":%.2f},"
          "\"hr\":{\"bpm\":%d,\"spo2\":%d},"
          "\"gsr\":{\"kalman\":%.1f,\"voltage\":%.3f},"
          "\"flex\":{\"ring\":%d,\"thumb\":%d,\"middle\":%d,\"pinky\":%d,\"index\":%d},"
          "\"skin_temp_c\":%.2f,"
          "\"gesture\":{\"hand_position\":\"%s\",\"gesture\":\"%s\"}"
        "},"
        "\"device_uptime_ms\":%lu,"
        "\"timestamp\":{\".sv\":\"timestamp\"}"
      "}",
      aX, aY, aZ,
      gX, gY, gZ,
      ir, (unsigned long)red,
      gsrRawLocal,
      fRaw[0], fRaw[1], fRaw[2], fRaw[3], fRaw[4],
      isnan(tempC) ? 0.0f : tempC,
      r, p, y,
      bpm, (int)spo2,
      gsrKalVal, vGSR,
      fKal[0], fKal[1], fKal[2], fKal[3], fKal[4],
      isnan(tempC) ? 0.0f : tempC,
      handStr, gestureTxt,
      (unsigned long)millis()
    );

    // ── PATCH to /s01.json ─────────────────────────────────────
    HTTPClient http;
    String url = String(FIREBASE_DB_URL) + "/s01.json";
    http.begin(sslClient, url);
    http.addHeader("Content-Type", "application/json");

    int httpCode = http.sendRequest("PATCH", json);
    if (httpCode == 200) {
      // success — silent
    } else if (httpCode > 0) {
      Serial.printf("WARN:Firebase HTTP %d\n", httpCode);
    } else {
      Serial.printf("WARN:Firebase err: %s\n",
                     http.errorToString(httpCode).c_str());
    }
    http.end();

    vTaskDelay(pdMS_TO_TICKS(FIREBASE_SEND_INTERVAL_MS));
  }
}
