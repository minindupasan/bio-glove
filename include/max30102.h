#pragma once

#include <stdint.h>
#include <stdbool.h>
#include "MAX30105.h"

// ═══════════════════════════════════════════════════════════════
//  MAX30102
// ═══════════════════════════════════════════════════════════════

// Fixed at 0x1F (~6mA) for both LEDs — matches proven working sketch.
#define LED_AMPLITUDE  0x1F

// BPM ring buffer
const byte RATE_SIZE = 4;
extern byte     bpmRates[RATE_SIZE];
extern byte     rateSpot;
extern long     lastBeat;
extern float    beatsPerMinute;
extern int      bpmAvg;
extern uint32_t lastRedV;

// SpO2 rolling buffer
#define SPO2_LEN   100
#define SPO2_SHIFT  25
extern uint32_t irBuf[SPO2_LEN];
extern uint32_t redBuf[SPO2_LEN];
extern int32_t  spo2Val;
extern int8_t   validSPO2;
extern int32_t  hrSPO2;
extern int8_t   validHR;
extern int32_t  lastValidSPO2;
extern int      spo2Samples;
extern bool     bufferFull;
extern bool     maxReady;

extern MAX30105 maxSensor;

// Initialise the MAX30102 — returns true if found on I2C bus
bool maxSetup();

// Must be called every loop iteration.
// Returns true if a new FIFO sample was consumed (caller should print HR line).
// beat_out : set true if a beat was detected this call.
// ir_out   : the IR value that was used for beat detection (captured before nextSample()).
bool maxLoop(bool &beat_out, long &ir_out);

// Reads temperature from the MAX30102 die in Celsius.
float maxReadTemperature();
