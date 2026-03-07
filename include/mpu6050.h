#pragma once

#include <stdint.h>

// ═══════════════════════════════════════════════════════════════
//  MPU6050  (direct I2C, no library needed)
// ═══════════════════════════════════════════════════════════════
#define MPU_ADDR   0x68
#define SDA_PIN    8
#define SCL_PIN    9

// Processed sensor values (updated by mpuRead)
extern float accX, accY, accZ;
extern float gyX,  gyY,  gyZ;
extern float roll, pitch, yaw;
extern float gyroOffX, gyroOffY, gyroOffZ;

void mpuWrite(uint8_t reg, uint8_t val);
bool mpuRead();
void mpuCalibrate();
