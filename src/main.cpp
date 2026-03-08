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

#include "kalman.h"
#include "flex_sensor.h"
#include "mpu6050.h"
#include "gsr.h"
#include "max30102.h"

// ═══════════════════════════════════════════════════════════════
//  FREE RTOS
// ═══════════════════════════════════════════════════════════════
SemaphoreHandle_t i2cMutex;

// ═══════════════════════════════════════════════════════════════
//  TIMING
// ═══════════════════════════════════════════════════════════════
unsigned long tFlex = 0, tIMU = 0, tGSR = 0, tTemp = 0;

// ═══════════════════════════════════════════════════════════════
//  SETUP
// ═══════════════════════════════════════════════════════════════
void setup() {
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

  // ── Flex sensor Kalman warmup ─────────────────────────────
  for (int i = 0; i < 5; i++)
    flex[i].kf.seed((float)readFlex(i));
  for (int w = 0; w < 30; w++) {
    for (int i = 0; i < 5; i++)
      flex[i].kf.update((float)readFlex(i));
    delay(10);
  }

  Serial.println("READY");

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
    for (int i = 0; i < 5; i++) {
      int raw = readFlex(i);
      int kal = (int)roundf(flex[i].kf.update((float)raw));
      Serial.print(raw); Serial.print(",");
      Serial.print(kal);
      if (i < 4) Serial.print(",");
    }
    Serial.println();
  }

  // ── IMU @ 20 Hz ───────────────────────────────────────────
  if (now - tIMU >= 50) {
    tIMU = now;
    if (xSemaphoreTake(i2cMutex, portMAX_DELAY)) {
      if (mpuRead()) {
        Serial.printf("IMU:%.3f,%.3f,%.3f,%.3f,%.3f,%.3f,%.2f,%.2f,%.2f\n",
          accX, accY, accZ, gyX, gyY, gyZ, roll, pitch, yaw);
      }
      xSemaphoreGive(i2cMutex);
    }
  }

  // ── GSR @ 10 Hz ───────────────────────────────────────────
  if (now - tGSR >= 100) {
    tGSR = now;
    int raw = readAveraged(GSR_PIN, 10);
    int kal = (int)roundf(gsrKF.update((float)raw));
    float voltage = raw * (3.3f / 4095.0f);
    int   change  = raw - gsrBaseline;
    bool  spike   = (change < -200);
    Serial.printf("GSR:%d,%d,%.3f,%d,%d\n",
      raw, kal, voltage, change, spike ? 1 : 0);
  }

  // ── MAX30102 ──────────────────────────────────────────────
  if (maxReady) {
    if (outBeat) { // Flag is true if beat was hit recently
      outBeat = false; // Consume pulse
      Serial.printf("HR:%ld,%lu,%d,%d,%d,%d\n",
        outIR, lastRedV, outBPM, lastValidSPO2, 1, LED_AMPLITUDE);
    } else if (now % 100 == 0) { // Stream non-beat state less often (like 10Hz)
      Serial.printf("HR:%ld,%lu,%d,%d,%d,%d\n",
        outIR, lastRedV, outBPM, lastValidSPO2, 0, LED_AMPLITUDE);
    }
  }

  // ── TEMP @ 4 Hz ───────────────────────────────────────────
  if (maxReady && now - tTemp >= 250) {
    tTemp = now;
    float c = -999.0f;
    if (xSemaphoreTake(i2cMutex, portMAX_DELAY)) {
      c = maxReadTemperature();
      xSemaphoreGive(i2cMutex);
    }
    
    if (c > -100.0f) {
      float f = c * 9.0f / 5.0f + 32.0f;
      const char* st =
        c < 20.0f ? "SENSOR_ERROR" :
        c < 30.0f ? "NO_CONTACT"   :
        c < 34.0f ? "COLD_SKIN"    :
        c < 36.0f ? "COOL_SKIN"    :
        c < 37.5f ? "NORMAL"       :
        c < 38.5f ? "WARM"         : "FEVER";
      Serial.printf("TEMP:%.3f,%.3f,%s\n", c, f, st);
    }
  }
}
