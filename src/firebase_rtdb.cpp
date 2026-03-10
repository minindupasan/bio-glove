// ═══════════════════════════════════════════════════════════════
//  FIREBASE RTDB — sends real s01 + simulated s02-s05 data
//  Endpoint: PATCH /.json  (single batched request for all 5)
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
//  Simulation — per-student fixed offsets + random noise
// ═══════════════════════════════════════════════════════════════

struct StudentOffset {
  float accel[3];   // x, y, z (g)
  float gyro[3];    // x, y, z (°/s)
  float rpy[3];     // roll, pitch, yaw (°)
  int   gsrRaw;     // ADC counts
  int   bpm;
  int   spo2;
  long  ir;
  long  red;
  float tempC;
};

static const StudentOffset offsets[4] = {
  // s02: slightly elevated HR, warmer
  { {0.02f, -0.03f, 0.01f}, {0.5f, -0.3f, 0.6f}, {3.0f, -2.0f, 5.0f},
    120, 8, -1, 1500, 1000, 0.8f },
  // s03: lower HR, cooler
  { {-0.04f, 0.02f, -0.01f}, {-0.6f, 0.4f, -0.2f}, {-5.0f, 3.0f, -7.0f},
    -150, -10, 1, -1800, -1200, -1.2f },
  // s04: higher GSR (stressed), slightly higher HR
  { {0.03f, 0.04f, -0.03f}, {0.3f, -0.7f, 0.4f}, {6.0f, -4.0f, 8.0f},
    180, 5, -2, 800, 600, 0.5f },
  // s05: calm, lower HR
  { {-0.01f, -0.02f, 0.04f}, {-0.4f, 0.2f, -0.5f}, {-2.0f, 1.0f, -4.0f},
    -100, -7, 2, -1000, -800, -0.6f },
};

static float noise(float amp) {
  return ((float)esp_random() / (float)UINT32_MAX) * 2.0f * amp - amp;
}
static int noiseI(int amp) {
  return (int)noise((float)amp);
}
static float clampf(float v, float lo, float hi) {
  return v < lo ? lo : (v > hi ? hi : v);
}
static int clampi(int v, int lo, int hi) {
  return v < lo ? lo : (v > hi ? hi : v);
}

// ═══════════════════════════════════════════════════════════════
//  WiFi — managed inside firebaseTask
// ═══════════════════════════════════════════════════════════════
void wifiConnect() {}

// ═══════════════════════════════════════════════════════════════
//  Firebase RTDB Task
// ═══════════════════════════════════════════════════════════════
void firebaseTask(void *pv) {
  Serial.println("INFO:Firebase task waiting 5s for sensors to stabilise...");
  vTaskDelay(pdMS_TO_TICKS(5000));

  // ── Start WiFi ─────────────────────────────────────────────
  Serial.printf("INFO:Heap before WiFi: %lu\n", (unsigned long)ESP.getFreeHeap());
  WiFi.mode(WIFI_STA);
  WiFi.setTxPower(WIFI_POWER_8_5dBm);
  esp_wifi_set_ps(WIFI_PS_MAX_MODEM);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.println("INFO:WiFi started");
  Serial.printf("INFO:Heap after WiFi.begin: %lu\n", (unsigned long)ESP.getFreeHeap());

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

  // ── Static JSON buffer (avoids heap/PSRAM issues) ──────────
  static char json[5120];
  const size_t JSON_CAP = sizeof(json);
  Serial.printf("INFO:JSON buffer %u bytes (static)\n", (unsigned)JSON_CAP);

  for (;;) {
    // ── Wait for WiFi ────────────────────────────────────────
    if (WiFi.status() != WL_CONNECTED) {
      vTaskDelay(pdMS_TO_TICKS(5000));
      continue;
    }

    // ── Snapshot all sensor globals ──────────────────────────
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
    float safeTemp   = isnan(tempC) ? 0.0f : tempC;

    const char* handStr    = (hp == HAND_UP) ? "UP"
                           : (hp == HAND_DOWN) ? "DOWN" : "REST";
    const char* gestureTxt = gestureToText(gestureLocal);

    // ── Build batched JSON for all 5 students ────────────────
    int pos = 0;

    // ── s01: real data (with flex) ───────────────────────────
    pos += snprintf(json + pos, JSON_CAP - pos,
      "{\"s01\":{"
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
      safeTemp,
      r, p, y,
      bpm, (int)spo2,
      gsrKalVal, vGSR,
      fKal[0], fKal[1], fKal[2], fKal[3], fKal[4],
      safeTemp,
      handStr, gestureTxt,
      (unsigned long)millis()
    );

    // ── s02–s05: simulated (no flex) ─────────────────────────
    static const char *simIds[] = {"s02", "s03", "s04", "s05"};
    for (int s = 0; s < 4; s++) {
      const StudentOffset &o = offsets[s];

      float sAx = aX + o.accel[0] + noise(0.02f);
      float sAy = aY + o.accel[1] + noise(0.02f);
      float sAz = aZ + o.accel[2] + noise(0.02f);
      float sGx = gX + o.gyro[0]  + noise(0.5f);
      float sGy = gY + o.gyro[1]  + noise(0.5f);
      float sGz = gZ + o.gyro[2]  + noise(0.5f);
      float sR  = r  + o.rpy[0]   + noise(2.0f);
      float sP  = p  + o.rpy[1]   + noise(1.5f);
      float sY  = y  + o.rpy[2]   + noise(3.0f);

      int sGsr  = clampi(gsrRawLocal + o.gsrRaw + noiseI(40), 0, 4095);
      int sBpm  = clampi(bpm + o.bpm + noiseI(3), 45, 120);
      int sSpo2 = clampi((int)spo2 + o.spo2 + noiseI(1), 88, 100);

      long sIrRaw  = ir + o.ir + (long)noiseI(500);
      long sIr     = sIrRaw < 0 ? 0 : sIrRaw;
      long sRedRaw = (long)red + o.red + (long)noiseI(400);
      unsigned long sRed = sRedRaw < 0 ? 0 : (unsigned long)sRedRaw;

      float sTemp   = clampf(safeTemp + o.tempC + noise(0.3f), 30.0f, 42.0f);
      float sGsrKal = clampf(gsrKalVal + (float)o.gsrRaw + noise(20.0f), 0.0f, 4095.0f);
      float sVgsr   = sGsr * (3.3f / 4095.0f);

      pos += snprintf(json + pos, JSON_CAP - pos,
        ",\"%s\":{"
          "\"raw\":{"
            "\"accel\":{\"x\":%.3f,\"y\":%.3f,\"z\":%.3f},"
            "\"gyro\":{\"x\":%.3f,\"y\":%.3f,\"z\":%.3f},"
            "\"max30102\":{\"ir\":%ld,\"red\":%lu},"
            "\"gsr\":%d,"
            "\"skin_temp_c\":%.2f"
          "},"
          "\"processed\":{"
            "\"imu\":{\"roll\":%.2f,\"pitch\":%.2f,\"yaw\":%.2f},"
            "\"hr\":{\"bpm\":%d,\"spo2\":%d},"
            "\"gsr\":{\"kalman\":%.1f,\"voltage\":%.3f},"
            "\"skin_temp_c\":%.2f,"
            "\"gesture\":{\"hand_position\":\"%s\",\"gesture\":\"%s\"}"
          "},"
          "\"device_uptime_ms\":%lu,"
          "\"timestamp\":{\".sv\":\"timestamp\"}"
        "}",
        simIds[s],
        sAx, sAy, sAz,
        sGx, sGy, sGz,
        sIr, sRed,
        sGsr,
        sTemp,
        sR, sP, sY,
        sBpm, sSpo2,
        sGsrKal, sVgsr,
        sTemp,
        handStr, gestureTxt,
        (unsigned long)millis()
      );
    }

    // Close outer object
    pos += snprintf(json + pos, JSON_CAP - pos, "}");

    Serial.printf("INFO:JSON size=%d bytes\n", pos);

    // ── PATCH to /.json (all 5 students in one request) ──────
    HTTPClient http;
    String url = String(FIREBASE_DB_URL) + "/.json";
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
