#pragma once

#include <Arduino.h>
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
extern volatile int     outBPM;
extern volatile long    outIR;
extern volatile bool    outBeat;
extern uint32_t         lastRedV;

// SpO2 rolling buffer
extern volatile int32_t lastValidSPO2;
extern bool     maxReady;

extern MAX30105 maxSensor;

// Initialise the MAX30102 — returns true if found on I2C bus
bool maxSetup();

// Endless FreeRTOS task that continuously reads MAX30102 hardware FIFO
void maxTask(void *pvParameters);

// Reads temperature from the MAX30102 die in Celsius.
float maxReadTemperature();
