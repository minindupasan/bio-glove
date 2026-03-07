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
  // ── Match proven working sketch exactly ─────────────────────────────────────
  if (!maxSensor.begin(Wire, I2C_SPEED_FAST)) return false;

  maxSensor.setup();                          // default: 0x1F, avg=4, mode=3, 400sps, 411µs, adc=4096
  maxSensor.setPulseAmplitudeRed(0x0A);       // Red LED low — indicates sensor running

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

  // Pump hardware FIFO into library sense buffer (non-blocking)
  maxSensor.check();

  // Only process when a NEW sample is available — one checkForBeat per real
  // sample, exactly like the proven working sketch.
  if (!maxSensor.available()) return false;

  // ── Read both channels from the SAME sample (non-blocking) ──────────────────
  long     irValue = (long)maxSensor.getFIFOIR();    // IR channel — matches working sketch's getIR()
  uint32_t redV    = maxSensor.getFIFORed();
  maxSensor.nextSample();

  ir_out   = irValue;
  lastRedV = redV;

  // ── Beat detection — exact same logic as proven working sketch ──────────────
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

    // ── SpO2 rolling buffer ───────────────────────────────────────────────────
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
    bpmAvg        = 0;
    rateSpot      = 0;
    memset(bpmRates, 0, sizeof(bpmRates));
    lastValidSPO2 = 0;
    bufferFull    = false;
    spo2Samples   = 0;
    memset(irBuf,  0, sizeof(irBuf));
    memset(redBuf, 0, sizeof(redBuf));
  }

  return true;
}
