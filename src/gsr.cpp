#include "gsr.h"
#include "flex_sensor.h"  // for readAveraged()
#include <Arduino.h>

int    gsrBaseline = 2048;
Kalman gsrKF(1.0f, 30.0f);

void gsrCalibrate() {
  long sum = 0;
  for (int i = 0; i < 100; i++) { sum += analogRead(GSR_PIN); delay(10); }
  gsrBaseline = sum / 100;
  gsrKF.seed((float)gsrBaseline);
}
