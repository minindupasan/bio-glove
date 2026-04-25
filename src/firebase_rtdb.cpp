// ═══════════════════════════════════════════════════════════════
//  FIREBASE RTDB — overwrites /glove/{sid} with latest readings.
//  No timestamps — SVC model runs per-frame, history not needed.
//  All 5 students batched in one PATCH to /glove.json
// ═══════════════════════════════════════════════════════════════

#include <Arduino.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <esp_wifi.h>
#include <esp_random.h>

#include "firebase_config.h"
#include "firebase_rtdb.h"
#include "flex_sensor.h"
#include "mpu6050.h"
#include "gsr.h"
#include "max30102.h"

extern float              g_tempC;
extern SemaphoreHandle_t  i2cMutex;

// ═══════════════════════════════════════════════════════════════
//  Simulation — per-student fixed offsets + small noise
// ═══════════════════════════════════════════════════════════════

struct StudentOffset {
  int   gsrRaw;
  float gsrKal;
  int   bpm;
  int   spo2;
  float tempC;
};

static const StudentOffset offsets[4] = {
  // s02: slightly elevated HR, warmer
  {  120,  120.0f,  8, -1,  0.8f },
  // s03: lower HR, cooler
  { -150, -150.0f, -10, 1, -1.2f },
  // s04: higher GSR (stressed), slightly higher HR
  {  180,  180.0f,  5, -2,  0.5f },
  // s05: calm, lower HR
  { -100, -100.0f, -7,  2, -0.6f },
};

static float noise(float amp) {
  return ((float)esp_random() / (float)UINT32_MAX) * 2.0f * amp - amp;
}
static int noiseI(int amp) { return (int)noise((float)amp); }
static float clampf(float v, float lo, float hi) {
  return v < lo ? lo : (v > hi ? hi : v);
}
static int clampi(int v, int lo, int hi) {
  return v < lo ? lo : (v > hi ? hi : v);
}

// ═══════════════════════════════════════════════════════════════
//  WiFi stub (connection managed inside firebaseTask)
// ═══════════════════════════════════════════════════════════════
void wifiConnect() {}

// ═══════════════════════════════════════════════════════════════
//  Firebase RTDB Task
// ═══════════════════════════════════════════════════════════════
void firebaseTask(void *pv) {
  Serial.println("INFO:Firebase task waiting 5s for sensors to stabilise...");
  vTaskDelay(pdMS_TO_TICKS(5000));

  Serial.printf("INFO:Heap before WiFi: %lu\n", (unsigned long)ESP.getFreeHeap());
  WiFi.mode(WIFI_STA);
  WiFi.setTxPower(WIFI_POWER_8_5dBm);
  esp_wifi_set_ps(WIFI_PS_MAX_MODEM);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.println("INFO:WiFi started");

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
  sslClient.setInsecure();

  // Buffer: 5 students × ~120 bytes each + wrapper ~50 bytes = ~700 bytes
  static char json[1024];
  const size_t JSON_CAP = sizeof(json);

  for (;;) {
    if (WiFi.status() != WL_CONNECTED) {
      vTaskDelay(pdMS_TO_TICKS(5000));
      continue;
    }

    // ── Snapshot sensor globals ───────────────────────────────
    int gsrRawLocal;
    portENTER_CRITICAL(&g_flexMux);
    gsrRawLocal = g_gsrRaw;
    portEXIT_CRITICAL(&g_flexMux);

    int      bpm    = outBPM;
    int32_t  spo2   = lastValidSPO2;
    float    tempC  = g_tempC;
    float    safeT  = isnan(tempC) ? 32.0f : tempC;
    float    gsrKal = gsrKF.x;
    float    vGSR   = gsrRawLocal * (3.3f / 4095.0f);

    // ── Build JSON: { "s01": {...}, "s02": {...}, ... } ───────
    int pos = 0;

    // s01 — real sensor data
    pos += snprintf(json + pos, JSON_CAP - pos,
      "{"
        "\"s01\":{"
          "\"gsr_raw\":%d,"
          "\"gsr_kal\":%.1f,"
          "\"gsr_voltage\":%.3f,"
          "\"bpm\":%d,"
          "\"spo2\":%d,"
          "\"skin_temp_c\":%.2f,"
          "\"ts\":{\".sv\":\"timestamp\"}"
        "}",
      gsrRawLocal,
      gsrKal,
      vGSR,
      bpm,
      (int)spo2,
      safeT
    );

    // s02–s05 — simulated
    static const char *simIds[] = { "s02", "s03", "s04", "s05" };
    for (int s = 0; s < 4; s++) {
      const StudentOffset &o = offsets[s];

      int   sGsr    = clampi(gsrRawLocal + o.gsrRaw + noiseI(30), 0, 4095);
      float sGsrKal = clampf(gsrKal + o.gsrKal + noise(15.0f), 0.0f, 4095.0f);
      float sVgsr   = sGsr * (3.3f / 4095.0f);
      int   sBpm    = clampi(bpm  + o.bpm  + noiseI(3), 45, 120);
      int   sSpo2   = clampi((int)spo2 + o.spo2 + noiseI(1), 88, 100);
      float sTemp   = clampf(safeT + o.tempC + noise(0.2f), 30.0f, 42.0f);

      pos += snprintf(json + pos, JSON_CAP - pos,
        ",\"%s\":{"
          "\"gsr_raw\":%d,"
          "\"gsr_kal\":%.1f,"
          "\"gsr_voltage\":%.3f,"
          "\"bpm\":%d,"
          "\"spo2\":%d,"
          "\"skin_temp_c\":%.2f,"
          "\"ts\":{\".sv\":\"timestamp\"}"
        "}",
        simIds[s],
        sGsr, sGsrKal, sVgsr,
        sBpm, sSpo2, sTemp
      );
    }

    pos += snprintf(json + pos, JSON_CAP - pos, "}");

    Serial.printf("INFO:Firebase JSON %d bytes\n", pos);

    // ── PATCH to /glove.json (overwrites each student node) ───
    HTTPClient http;
    String url = String(FIREBASE_DB_URL) + "/glove.json?auth=" + FIREBASE_AUTH;
    http.begin(sslClient, url);
    http.addHeader("Content-Type", "application/json");

    int httpCode = http.sendRequest("PATCH", json);
    if (httpCode == 200) {
      Serial.println("INFO:Firebase OK");
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
