#include <Arduino.h>
#include "max30102.h"

#include <freertos/task.h>
#include <freertos/semphr.h>
#include <stdint.h>
#include "heartRate.h"
#include "spo2_algorithm.h"

// ── MAX30102 sensor object ────────────────────────────────────────────────────
MAX30105 maxSensor;
extern SemaphoreHandle_t i2cMutex;

// ── BPM state ─────────────────────────────────────────────────────────────────
byte             bpmRates[RATE_SIZE] = {0};
byte             rateSpot            = 0;
long             lastBeat            = 0;
float            beatsPerMinute      = 0;
volatile int     outBPM              = 0;
volatile long    outIR               = 0;
volatile bool    outBeat             = false;
uint32_t         lastRedV            = 0;

// ── SpO2 state ─────────────────────────────────────────────────────────────────
#define SPO2_LEN   100
#define SPO2_SHIFT 25
uint32_t         irBuf[SPO2_LEN]   = {0};
uint32_t         redBuf[SPO2_LEN]  = {0};
int              spo2Samples       = 0;

volatile int32_t lastValidSPO2     = 0;
bool             maxReady          = false;

// ─────────────────────────────────────────────────────────────────────────────
bool maxSetup() {
  // ── Match proven working sketch exactly ─────────────────────────────────────
  if (!maxSensor.begin(Wire, I2C_SPEED_FAST)) return false;

  maxSensor.setup();                          // default: 0x1F, avg=4, mode=3, 400sps, 411µs, adc=4096
  maxSensor.setPulseAmplitudeRed(0x0A);       // Red LED low — indicates sensor running

  maxReady = true;
  return true;
}

void maxTask(void *pvParameters) {
  for (;;) {
    if (!maxReady) {
      vTaskDelay(pdMS_TO_TICKS(100));
      continue;
    }

    // Read newly available samples from the sensor's hardware FIFO
    if (xSemaphoreTake(i2cMutex, portMAX_DELAY)) {
      maxSensor.check();
      
      // Process ALL available samples to guarantee continuous FIR filter data for beat detection
      while (maxSensor.available()) {
        long irValue  = maxSensor.getFIFOIR();
        long redValue = maxSensor.getFIFORed();
        maxSensor.nextSample(); // Advance the library tail pointer
        
        outIR    = irValue; // Bubble up the last read IR value
        lastRedV = redValue;

        // ── Store sample for SpO2 calculation ──
        if (spo2Samples < SPO2_LEN) {
          irBuf[spo2Samples]  = irValue;
          redBuf[spo2Samples] = redValue;
          spo2Samples++;
        }

        // ── Calculate SpO2 when 100 samples are filled ──
        if (spo2Samples == SPO2_LEN) {
          int32_t spo2Val;
          int8_t  validSPO2;
          int32_t hrSPO2; // Not used as primary, just needed for the function
          int8_t  validHR;

          maxim_heart_rate_and_oxygen_saturation(irBuf, SPO2_LEN, redBuf, &spo2Val, &validSPO2, &hrSPO2, &validHR);

          if (validSPO2 && spo2Val > 0 && spo2Val <= 100) {
            lastValidSPO2 = spo2Val;
          }

          // Shift samples to keep 75 older samples and free 25 spots for overlap
          for (byte i = SPO2_SHIFT; i < SPO2_LEN; i++) {
            irBuf[i - SPO2_SHIFT]  = irBuf[i];
            redBuf[i - SPO2_SHIFT] = redBuf[i];
          }
          spo2Samples = SPO2_LEN - SPO2_SHIFT;
        }

        // ── BPM calculation ────────────────────
        if (irValue > 7000) {                     // If a finger is detected
          if (checkForBeat(irValue) == true) {    // If a heart beat is detected
            outBeat = true; // Stay true if ANY sample in the burst triggered a beat

            long delta = millis() - lastBeat;     // Measure duration between two beats
            lastBeat = millis();

            beatsPerMinute = 60.0f / (delta / 1000.0f);

            if (beatsPerMinute < 255 && beatsPerMinute > 20) {
              bpmRates[rateSpot++] = (byte)beatsPerMinute; // Store reading
              rateSpot %= RATE_SIZE;                       // Wrap variable

              // Take average of readings
              int tempAvg = 0;
              for (byte x = 0; x < RATE_SIZE; x++)
                tempAvg += bpmRates[x];
              tempAvg /= RATE_SIZE;
              
              outBPM = tempAvg;
            }
          }
        } 
        else {
          // If no finger is detected
          outBPM = 0;
        }
      }
      xSemaphoreGive(i2cMutex);
    }
    
    // Slight sleep to allow other tasks to yield and prevent Core 0 watchdog timeout
    vTaskDelay(pdMS_TO_TICKS(5)); 
  }
}

// ─────────────────────────────────────────────────────────────────────────────
float maxReadTemperature() {
  if (!maxReady) return -999.0f;
  return maxSensor.readTemperature();
}
