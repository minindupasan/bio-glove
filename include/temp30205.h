#pragma once

#include <stdint.h>
#include <stdbool.h>

// ═══════════════════════════════════════════════════════════════
//  CJMCU-30205
// ═══════════════════════════════════════════════════════════════
#define TEMP_ADDR  0x4C
#define TEMP_REG   0x00
#define TEMP_CFG   0x01

extern bool tempReady;

// Returns true if sensor found and configured
bool tempInit();

// Returns temperature in °C, or -999.0f on I2C error
float readTempC();
