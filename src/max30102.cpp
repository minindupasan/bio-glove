#include "max30102.h"

#include <Arduino.h>
#include <Wire.h>
#include "heartRate.h"
#include "spo2_algorithm.h"

// ── MAX30102 sensor object ────────────────────────────────────────────────────
MAX30105 maxSensor;

// ── BPM state ─────────────────────────────────────────────────────────────────
byte     bpmRates[RATE_SIZE] = {0};
byte     rateSpot            = 0;
long     lastBeat            = 0;
float    beatsPerMinute      = 0;
int      bpmAvg              = 0;
uint32_t lastRedV            = 0;

// ── SpO2 state ─────────────────────────────────────────────────────────────────
uint32_t irBuf[SPO2_LEN]  = {0};
uint32_t redBuf[SPO2_LEN] = {0};
int32_t  spo2Val           = 0;
int8_t   validSPO2         = 0;
int32_t  hrSPO2            = 0;
int8_t   validHR           = 0;
int32_t  lastValidSPO2     = 0;
int      spo2Samples       = 0;
bool     bufferFull        = false;
bool     maxReady          = false;

// ─────────────────────────────────────────────────────────────────────────────
bool maxSetup() {
  if (!maxSensor.begin(Wire, I2C_SPEED_STANDARD)) return false;

  // sampleRate=400 is the critical fix:
  //   400 Hz ÷ sampleAvg=4 → 100 output samples/sec
  //   A 70 BPM systolic rise (~100 ms) spans ~10 output samples
  //   → checkForBeat() can clearly see the slope change
  // adcRange=4096 (was 16384): higher sensitivity per count → larger AC swing
  maxSensor.setup(0x1F, 4, 2, 400, 411, 4096);

  // Both LEDs fixed at 0x1F (~6 mA) — working sketch approach
  maxSensor.setPulseAmplitudeRed(LED_AMPLITUDE);
  maxSensor.setPulseAmplitudeIR(LED_AMPLITUDE);
  maxSensor.setPulseAmplitudeGreen(0);

  // Clear buffers — loop fills them naturally once finger is placed
  memset(irBuf,  0, sizeof(irBuf));
  memset(redBuf, 0, sizeof(redBuf));

  maxReady = true;
  return true;
}

// ─────────────────────────────────────────────────────────────────────────────
bool maxLoop(bool &beat_out, long &ir_out) {
  beat_out = false;
  ir_out   = 0;

  maxSensor.check();  // pump FIFO into library buffer

  // ── Read IR every loop — matches OLED sketch pattern ────────────────────────
  // getRed() is actually the IR channel on this board (IR/RED are swapped).
  // Evidence: serial showed RED=6130 >> IR=478 with no finger.
  // Captured BEFORE nextSample() so the value is fresh.
  long irValue = maxSensor.getRed();
  ir_out = irValue;

  // ── Beat detection every loop — no beats missed ─────────────────────────────
  if (irValue > 7000) {
    if (checkForBeat(irValue)) {
      long delta       = millis() - lastBeat;
      lastBeat         = millis();
      beatsPerMinute   = 60.0f / (delta / 1000.0f);
      if (beatsPerMinute > 20 && beatsPerMinute < 255) {
        bpmRates[rateSpot++] = (byte)beatsPerMinute;
        rateSpot %= RATE_SIZE;
        bpmAvg = 0;
        for (byte x = 0; x < RATE_SIZE; x++) bpmAvg += bpmRates[x];
        bpmAvg /= RATE_SIZE;
        beat_out = true;
      }
    }
  }

  // ── FIFO drain: SpO2 buffer + report when new sample is ready ───────────────
  // Print ONLY when a new sample is ready (~100×/sec with avg=4 at 400 Hz).
  // Prevents serial flood that was corrupting FLEX/IMU lines.
  if (!maxSensor.available()) return false;

  // Swap: getIR() reads the Red channel on this board
  uint32_t redV = maxSensor.getIR();
  lastRedV = redV;
  maxSensor.nextSample();

  if (irValue > 7000) {
    memmove(irBuf,  irBuf  + 1, (SPO2_LEN - 1) * sizeof(uint32_t));
    memmove(redBuf, redBuf + 1, (SPO2_LEN - 1) * sizeof(uint32_t));
    irBuf[SPO2_LEN  - 1] = (uint32_t)irValue;
    redBuf[SPO2_LEN - 1] = redV;
    spo2Samples++;
    if (!bufferFull && spo2Samples >= SPO2_LEN) {
      bufferFull  = true;
      spo2Samples = 0;
    }
    if (bufferFull && spo2Samples >= SPO2_SHIFT) {
      spo2Samples = 0;
      maxim_heart_rate_and_oxygen_saturation(
        irBuf, SPO2_LEN, redBuf,
        &spo2Val, &validSPO2, &hrSPO2, &validHR);
      if (validSPO2 && spo2Val >= 80 && spo2Val <= 100)
        lastValidSPO2 = spo2Val;
    }
  } else {
    // Finger removed — reset all state
    bpmAvg       = 0;
    rateSpot     = 0;
    memset(bpmRates, 0, sizeof(bpmRates));
    lastValidSPO2 = 0;
    bufferFull    = false;
    spo2Samples   = 0;
    memset(irBuf,  0, sizeof(irBuf));
    memset(redBuf, 0, sizeof(redBuf));
  }

  // Caller prints HR line using irValue, lastRedV, bpmAvg, lastValidSPO2, beat_out
  return true;
}
