#include "temp30205.h"
#include <Arduino.h>
#include <Wire.h>

bool tempReady = false;

static bool tempWrite(uint8_t reg, uint16_t val) {
  Wire.beginTransmission(TEMP_ADDR);
  Wire.write(reg);
  Wire.write((val >> 8) & 0xFF);
  Wire.write(val & 0xFF);
  return Wire.endTransmission() == 0;
}

bool tempInit() {
  Wire.beginTransmission(TEMP_ADDR);
  if (Wire.endTransmission() != 0) return false;
  tempWrite(TEMP_CFG, 0x0000);
  tempReady = true;
  return true;
}

float readTempC() {
  Wire.beginTransmission(TEMP_ADDR);
  Wire.write(TEMP_REG);
  Wire.endTransmission(false);
  Wire.requestFrom((uint8_t)TEMP_ADDR, (uint8_t)2);
  if (Wire.available() >= 2) {
    int16_t raw = ((int16_t)Wire.read() << 8) | Wire.read();
    return raw * 0.00390625f;
  }
  return -999.0f;
}
