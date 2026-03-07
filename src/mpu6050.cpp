#include "mpu6050.h"
#include <Arduino.h>
#include <Wire.h>
#include <math.h>

float accX = 0, accY = 0, accZ = 0;
float gyX  = 0, gyY  = 0, gyZ  = 0;
float roll = 0, pitch = 0, yaw = 0;
float gyroOffX = 0, gyroOffY = 0, gyroOffZ = 0;

void mpuWrite(uint8_t reg, uint8_t val) {
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(reg); Wire.write(val);
  Wire.endTransmission();
}

bool mpuRead() {
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(0x3B);
  if (Wire.endTransmission(false) != 0) return false;
  Wire.requestFrom((uint8_t)MPU_ADDR, (uint8_t)14);
  if (Wire.available() < 14) return false;

  int16_t ax = (Wire.read() << 8) | Wire.read();
  int16_t ay = (Wire.read() << 8) | Wire.read();
  int16_t az = (Wire.read() << 8) | Wire.read();
  Wire.read(); Wire.read(); // temp register — discard
  int16_t gx = (Wire.read() << 8) | Wire.read();
  int16_t gy = (Wire.read() << 8) | Wire.read();
  int16_t gz = (Wire.read() << 8) | Wire.read();

  accX = ax / 16384.0f;
  accY = ay / 16384.0f;
  accZ = az / 16384.0f;
  gyX  = gx / 131.0f - gyroOffX;
  gyY  = gy / 131.0f - gyroOffY;
  gyZ  = gz / 131.0f - gyroOffZ;

  roll  = atan2(accY, accZ) * 180.0f / PI;
  pitch = atan2(-accX, sqrt(accY * accY + accZ * accZ)) * 180.0f / PI;
  yaw  += gyZ * 0.05f;  // integrate at 20 Hz
  return true;
}

void mpuCalibrate() {
  float sx = 0, sy = 0, sz = 0;
  for (int i = 0; i < 200; i++) {
    mpuRead();
    sx += gyX; sy += gyY; sz += gyZ;
    delay(5);
  }
  gyroOffX = sx / 200.0f;
  gyroOffY = sy / 200.0f;
  gyroOffZ = sz / 200.0f;
}
