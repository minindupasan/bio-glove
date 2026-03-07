#pragma once

#include "kalman.h"

// ═══════════════════════════════════════════════════════════════
//  FLEX SENSORS
// ═══════════════════════════════════════════════════════════════
struct FlexSensor {
  int    pin;
  Kalman kf;
  int    avgN;  // hardware averaging — more samples = less noise, more latency
};

// GPIO1=Ring  GPIO2=Thumb  GPIO3=Middle  GPIO4=Pinky  GPIO6=Index
// All sensors: Q=1.0 R=100 avgN=20 — aggressive smoothing to kill ADC noise
// 15kΩ sensors (Thumb/Pinky) use R=150, avgN=32 for extra noise rejection
extern FlexSensor flex[5];

// Read `n` samples from `pin`, return average
int readAveraged(int pin, int n = 8);

// Read a flex sensor using its own per-sensor averaging count
int readFlex(int idx);
