// ╔══════════════════════════════════════════════════════════════╗
// ║  BIOSENSOR GLOVE — ESP32-S3 Combined Firmware               ║
// ║  Sensors: Flex x5 | MPU6050 | GSR | MAX30102 | CJMCU-30205  ║
// ╠══════════════════════════════════════════════════════════════╣
// ║  WIRING SUMMARY                                              ║
// ║  ── Flex Sensors ─────────────────────────────────────────── ║
// ║    3.3V ─[FlexSensor]─ ADC Pin ─[Pulldown]─ GND             ║
// ║    GPIO1 → Ring   (100kΩ flex, 100kΩ pull)                   ║
// ║    GPIO2 → Thumb  ( 15kΩ flex,  22kΩ pull)                   ║
// ║    GPIO3 → Middle (120kΩ flex, 100kΩ pull)                   ║
// ║    GPIO4 → Pinky  ( 15kΩ flex,  10kΩ pull)                   ║
// ║    GPIO6 → Index  (100kΩ flex, 100kΩ pull)                   ║
// ║  ── MPU6050 ─────────────────────────────────────────────── ║
// ║    SDA → GPIO8   SCL → GPIO9   VCC → 3.3V                  ║
// ║  ── GSR Grove v1.2 ──────────────────────────────────────── ║
// ║    SIG → GPIO5   VCC → 3.3V   GND → GND                    ║
// ║  ── MAX30102 ────────────────────────────────────────────── ║
// ║    SDA → GPIO8   SCL → GPIO9   VCC → 3.3V                  ║
// ║    (shares I2C bus with MPU6050 & CJMCU)                    ║
// ║  ── CJMCU-30205 ─────────────────────────────────────────── ║
// ║    SDA → GPIO8   SCL → GPIO9   A2 → 3.3V  A1,A0 → GND     ║
// ║    Address: 0x4C                                            ║
// ╠══════════════════════════════════════════════════════════════╣
// ║  SERIAL OUTPUT @ 115200 baud                                 ║
// ║  Line prefix determines type:                                ║
// ║    FLEX:r0,k0,...,r4,k4 (Ring,Thumb,Mid,Pinky,Index)(20Hz)  ║
// ║    IMU:ax,ay,az,gx,gy,gz,roll,pitch,yaw   (20 Hz)           ║
// ║    GSR:raw,kal,voltage,change,spike        (10 Hz)           ║
// ║    HR:ir,red,bpm,spo2,beat,led_amp         (on sample)       ║
// ║    TEMP:tempC,tempF,status                 (4 Hz)            ║
// ║    READY                                   (once on boot)    ║
// ╚══════════════════════════════════════════════════════════════╝

#include <Arduino.h>
#include <Wire.h>
#include "soc/soc.h"
#include "soc/rtc_cntl_reg.h"

#include "kalman.h"
#include "flex_sensor.h"
#include "mpu6050.h"
#include "gsr.h"
#include "max30102.h"
#include "firebase_rtdb.h"

// ═══════════════════════════════════════════════════════════════
//  FREE RTOS
// ═══════════════════════════════════════════════════════════════
SemaphoreHandle_t i2cMutex;

// ═══════════════════════════════════════════════════════════════
//  TIMING & CACHE
// ═══════════════════════════════════════════════════════════════
unsigned long tFlex = 0, tIMU = 0, tGSR = 0, tTemp = 0;
uint32_t lastPrintMs = 0;
float g_tempC = NAN;

// ═══════════════════════════════════════════════════════════════
//  SETUP
// ═══════════════════════════════════════════════════════════════
void setup() {
  WRITE_PERI_REG(RTC_CNTL_BROWN_OUT_REG, 0);  // disable brownout (WiFi current spike)

  Serial.begin(115200);
  delay(1200);

  // ADC
  analogReadResolution(12);
  analogSetAttenuation(ADC_11db);

  // I2C
  i2cMutex = xSemaphoreCreateMutex();
  if (i2cMutex == NULL) {
    Serial.println("WARN: Failed to create I2C Mutex");
  }

  Wire.begin(SDA_PIN, SCL_PIN);
  Wire.setClock(400000);
  delay(200);

  // ── MPU6050 init ──────────────────────────────────────────
  if (xSemaphoreTake(i2cMutex, portMAX_DELAY)) {
    mpuWrite(0x6B, 0x00);  // wake up
    mpuWrite(0x1C, 0x00);  // accel ±2g
    mpuWrite(0x1B, 0x00);  // gyro ±250°/s
    delay(100);
    Serial.println("INFO:Calibrating MPU6050 gyro (keep still)...");
    mpuCalibrate();
    Serial.println("INFO:MPU6050 ready");
    xSemaphoreGive(i2cMutex);
  }

  // ── MAX30102 init ─────────────────────────────────────────
  if (xSemaphoreTake(i2cMutex, portMAX_DELAY)) {
    if (maxSetup()) {
      Serial.println("INFO:MAX30102 ready");
    } else {
      Serial.println("WARN:MAX30102 not found");
    }
    xSemaphoreGive(i2cMutex);
  }

  // ── GSR baseline ─────────────────────────────────────────
  Serial.println("INFO:Calibrating GSR baseline...");
  gsrCalibrate();
  Serial.print("INFO:GSR baseline="); Serial.println(gsrBaseline);

  // ── Flex sensor Calibration ─────────────────────────────
  calibrateFlexSensors();

  Serial.println("READY");

  portENTER_CRITICAL(&g_flexMux);
  for (int i = 0; i < 5; i++) {
    flexBent[i] = false;
    flexChangeCount[i] = 0;
    flexRaw[i] = 0;
    flexDiff[i] = 0;
  }
  g_gsrRaw = 0;
  g_stableGesture = G_NONE;
  portEXIT_CRITICAL(&g_flexMux);

  xTaskCreatePinnedToCore(
    flexTask,
    "FlexTask",
    4096,
    NULL,
    1,
    NULL,
    0
  );

  // ── Launch MAX30102 task on Core 0 ────────────────────────
  if (maxReady) {
    xTaskCreatePinnedToCore(
      maxTask,      // Function
      "maxTask",    // Name
      4096,         // Stack size
      NULL,         // Parameters
      2,            // Priority
      NULL,         // Handle
      0             // Core 0 (default Arduino loop is Core 1)
    );
  }

  // ── Launch Firebase task on Core 0 ──────────────────────
  xTaskCreatePinnedToCore(
    firebaseTask,   // Function
    "firebaseTask", // Name
    8192,           // Stack size (HTTPS needs headroom)
    NULL,           // Parameters
    1,              // Priority
    NULL,           // Handle
    0               // Core 0
  );
}

// ═══════════════════════════════════════════════════════════════
//  LOOP
// ═══════════════════════════════════════════════════════════════
void loop() {
  unsigned long now = millis();

  // ── FLEX @ 20 Hz ──────────────────────────────────────────
  if (now - tFlex >= 50) {
    tFlex = now;
    Serial.print("FLEX:");
    portENTER_CRITICAL(&g_flexMux);
    for (int i = 0; i < 5; i++) {
      Serial.print(flexRaw[i]); Serial.print(",");
      Serial.print(flexKal[i]);
      if (i < 4) Serial.print(",");
    }
    portEXIT_CRITICAL(&g_flexMux);
    Serial.println();
  }

  // ── IMU @ 20 Hz ───────────────────────────────────────────
  if (now - tIMU >= 50) {
    tIMU = now;
    if (xSemaphoreTake(i2cMutex, portMAX_DELAY)) {
      if (mpuRead()) {
        // inverted mapping mapping for hand pos
        if (!isnan(pitch)) {
          if (pitch < -20.0f)         g_handPos = HAND_DOWN;
          else if (pitch > 20.0f)     g_handPos = HAND_UP;
          else                        g_handPos = HAND_REST;
        }
        
        // Print IMU format for Processing Dashboard:
        // IMU:ax,ay,az,gx,gy,gz,roll,pitch,yaw
        Serial.printf("IMU:%.3f,%.3f,%.3f,%.3f,%.3f,%.3f,%.2f,%.2f,%.2f\n",
          accX, accY, accZ, gyX, gyY, gyZ, roll, pitch, yaw);
      }
      xSemaphoreGive(i2cMutex);
    }
  }

  // ── TEMP @ 2 Hz ───────────────────────────────────────────
  if (maxReady && now - tTemp >= 500) {
    tTemp = now;
    float c = -999.0f;
    if (xSemaphoreTake(i2cMutex, portMAX_DELAY)) {
      c = maxReadTemperature();
      xSemaphoreGive(i2cMutex);
    }
    
    if (c > -100.0f) { // Valid temp
       g_tempC = c;
    }
  }

  // ── Serial Output @ 10 Hz ───────────────────────────────────────────
  if (now - lastPrintMs >= 100) {
    lastPrintMs = now;

    bool bentLocal[5];
    int  diffLocal[5];
    uint8_t stableGestureLocal;
    int gsrRawLocal;

    // Read globals atomically
    portENTER_CRITICAL(&g_flexMux);
    for (int i = 0; i < 5; i++) {
      bentLocal[i] = flexBent[i];
      diffLocal[i] = flexDiff[i];
    }
    stableGestureLocal = g_stableGesture;
    gsrRawLocal = g_gsrRaw;
    portEXIT_CRITICAL(&g_flexMux);

    HandPos hp = g_handPos;
    const char* handStr = (hp == HAND_UP) ? "UP" : (hp == HAND_DOWN) ? "DOWN" : "REST";
    const char* gestureTxt = gestureToText(stableGestureLocal);

    Serial.print("HAND=");
    Serial.print(handStr);

    // Our array map: Ring=0, Thumb=1, Middle=2, Pinky=3, Index=4
    // User format request: T I M R
    Serial.print("  FLEX[T I M R]=");
    Serial.print(bentLocal[1] ? "B(" : "N("); Serial.print(diffLocal[1]); Serial.print(") "); // Thumb
    Serial.print(bentLocal[4] ? "B(" : "N("); Serial.print(diffLocal[4]); Serial.print(") "); // Index
    Serial.print(bentLocal[2] ? "B(" : "N("); Serial.print(diffLocal[2]); Serial.print(") "); // Middle
    Serial.print(bentLocal[0] ? "B(" : "N("); Serial.print(diffLocal[0]); Serial.print(")");  // Ring

    Serial.print("  GSR=");
    Serial.print(gsrRawLocal);

    Serial.print("  TEMP=");
    if (isnan(g_tempC)) Serial.print("NA");
    else Serial.print(g_tempC, 2);
    Serial.print("C");

    Serial.print("  BPM=");
    Serial.print(outBPM);
    Serial.print(" SPO2=");
    Serial.print(lastValidSPO2);

    Serial.print("  GESTURE=");
    Serial.println(gestureTxt);

    // New format for Processing parser
    Serial.print("GESTURE:");
    Serial.println(gestureTxt);

    // RESTORE DASHBOARD DATA STREAMS:
    // TEMP:34.188,93.537,COOL_SKIN
    if (!isnan(g_tempC)) {
      Serial.printf("TEMP:%.3f,%.3f,OK\n", g_tempC, g_tempC * 1.8f + 32.0f);
    }
    
    // GSR:raw,kal,voltage,change,spike
    float vGSR = gsrRawLocal * (3.3f / 4095.0f);
    Serial.printf("GSR:%d,%d,%.3f,0,0\n", gsrRawLocal, gsrRawLocal, vGSR);
    
    // HR:ir,red,bpm,spo2,beat,led_amp
    Serial.printf("HR:%ld,0,%d,%d,%d,31\n", outIR, outBPM, lastValidSPO2, outBeat ? 1 : 0);

    if (outBeat) outBeat = false; // consume
  }
}
