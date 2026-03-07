#include "flex_sensor.h"
#include <Arduino.h>

// GPIO1=Ring  GPIO2=Thumb  GPIO3=Middle  GPIO4=Pinky  GPIO6=Index
// All sensors: Q=1.0 R=100 avgN=20 — aggressive smoothing to kill ADC noise
// 15kΩ sensors (Thumb/Pinky) use R=150, avgN=32 for extra noise rejection
FlexSensor flex[5] = {
  {1, Kalman(1.0f, 100.0f), 20},  // [0] Ring   — GPIO1, 100kΩ flex, 100kΩ pull
  {2, Kalman(1.0f, 150.0f), 32},  // [1] Thumb  — GPIO2,  15kΩ flex,  22kΩ pull
  {3, Kalman(1.0f, 100.0f), 20},  // [2] Middle — GPIO3, 120kΩ flex, 100kΩ pull
  {4, Kalman(1.0f, 150.0f), 32},  // [3] Pinky  — GPIO4,  15kΩ flex,  10kΩ pull
  {6, Kalman(1.0f, 100.0f), 20},  // [4] Index  — GPIO6, 100kΩ flex, 100kΩ pull
};

int readAveraged(int pin, int n) {
  long s = 0;
  for (int i = 0; i < n; i++) { s += analogRead(pin); delayMicroseconds(150); }
  return s / n;
}

int readFlex(int idx) {
  return readAveraged(flex[idx].pin, flex[idx].avgN);
}
