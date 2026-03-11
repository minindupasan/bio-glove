#include "flex_sensor.h"
#include <Arduino.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include "mpu6050.h"
#include "gsr.h"

// ═══════════════════════════════════════════════════════════════
//  GESTURES & HAND STATE VARIABLES
// ═══════════════════════════════════════════════════════════════
volatile HandPos g_handPos = HAND_DOWN;
portMUX_TYPE g_flexMux = portMUX_INITIALIZER_UNLOCKED;

int  flexBaseline[5] = {0};
int  flexRaw[5]      = {0};
int  flexKal[5]      = {0};
int  flexDiff[5]     = {0};
bool flexBent[5]     = {false};
uint8_t flexChangeCount[5] = {0};

uint8_t g_stableGesture = G_NONE;
uint8_t g_lastCandidate = G_NONE;
uint8_t g_candidateCount = 0;

volatile int g_gsrRaw = 0;

int FLEX_TH_FINGER[5] = {
  100, // Ring (index 0)
  100, // Thumb (index 1)
  100, // Middle (index 2)
  100, // Pinky (index 3 - not used in gestures, but defined)
  100  // Index (index 4)
};

// Dashboard calibration: midpoint between rest and bend for each finger
// Bent = reading crossed past midpoint toward the bend side
int  flexMidpoint[5] = {0};
int  flexBendDir[5]  = {-1, -1, -1, -1, -1};  // -1 = readings drop when bent, +1 = rise
bool flexHasDashCal  = false;
static const int FLEX_STABLE_N    = 3;
static const int GESTURE_STABLE_N = 3;

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

// ═══════════════════════════════════════════════════════════════
//  CALIBRATION
// ═══════════════════════════════════════════════════════════════
void calibrateFlexSensors() {
  Serial.println("\nINFO: Flex baseline: Keep Thumb+Index+Middle+Ring STRAIGHT...");
  
  long bsum[5] = {0};
  const int FLEX_BASELINE_ITERS = 400;

  for (int k = 0; k < FLEX_BASELINE_ITERS; k++) {
    for (int i = 0; i < 5; i++) {
      bsum[i] += readFlex(i);
    }
    delay(10);
    if ((k % 25) == 0) Serial.print(".");
  }
  Serial.println();

  for (int i = 0; i < 5; i++) {
    flexBaseline[i] = (int)(bsum[i] / FLEX_BASELINE_ITERS);
  }

  Serial.print("INFO: Baseline [Ring Thumb Mid Pinky Index] = [ ");
  for (int i = 0; i < 5; i++) {
    Serial.print(flexBaseline[i]);
    Serial.print(" ");
  }
  Serial.println("]");
}

// ═══════════════════════════════════════════════════════════════
//  APPLY CALIBRATION FROM PROCESSING DASHBOARD
//  Format: SETCAL:rest0,rest1,rest2,rest3,rest4,bend0,bend1,bend2,bend3,bend4
// ═══════════════════════════════════════════════════════════════
void applyDashboardCal(const char* payload) {
  int rest[5], bend[5];
  int n = sscanf(payload, "%d,%d,%d,%d,%d,%d,%d,%d,%d,%d",
    &rest[0], &rest[1], &rest[2], &rest[3], &rest[4],
    &bend[0], &bend[1], &bend[2], &bend[3], &bend[4]);
  if (n != 10) {
    Serial.println("WARN:SETCAL bad format");
    return;
  }

  portENTER_CRITICAL(&g_flexMux);
  for (int i = 0; i < 5; i++) {
    flexBaseline[i] = rest[i];
    flexMidpoint[i] = (rest[i] + bend[i]) / 2;
    flexBendDir[i]  = (bend[i] < rest[i]) ? -1 : 1;  // -1=drop, +1=rise
    // Reset debounce state so new thresholds take effect immediately
    flexBent[i] = false;
    flexChangeCount[i] = 0;
  }
  flexHasDashCal = true;
  portEXIT_CRITICAL(&g_flexMux);

  Serial.print("INFO:Dashboard CAL applied. Rest=[");
  for (int i = 0; i < 5; i++) { Serial.print(rest[i]); if (i<4) Serial.print(","); }
  Serial.print("] Bend=[");
  for (int i = 0; i < 5; i++) { Serial.print(bend[i]); if (i<4) Serial.print(","); }
  Serial.print("] Mid=[");
  for (int i = 0; i < 5; i++) { Serial.print(flexMidpoint[i]); if (i<4) Serial.print(","); }
  Serial.print("] Dir=[");
  for (int i = 0; i < 5; i++) { Serial.print(flexBendDir[i]>0?"UP":"DN"); if (i<4) Serial.print(","); }
  Serial.println("]");
  Serial.println("CALACK:OK");
}

// ═══════════════════════════════════════════════════════════════
//  GESTURE DEBOUNCING & MAPPING
// ═══════════════════════════════════════════════════════════════
const char* gestureToText(uint8_t g) {
  switch (g) {
    case G_QUESTION:   return "QUESTION";
    case G_YES:        return "YES";
    case G_NO:         return "NO";
    case G_INC_AC:     return "INC AC";
    case G_DEC_AC:     return "DEC AC";
    case G_INC_LIGHT:  return "INC LIGHT";
    case G_DEC_LIGHT:  return "DEC LIGHT";
    default:           return "-";
  }
}


static uint8_t gestureIdFromState(HandPos hp, bool T_straight, bool I_straight, bool M_straight, bool R_straight, bool P_straight) {
  // Common gyro orientation for YES (Thumbs Up)
  bool yes_orientation = (roll > -145.0f && roll < -55.0f && pitch > -45.0f && pitch < 15.0f);
  // NO gesture is exact positive range of YES gesture
  bool no_orientation = (roll > 55.0f && roll < 145.0f && pitch > -15.0f && pitch < 45.0f);

  // YES: thumb straight + Index/Middle/Ring/Pinky bent + gyro orientation
  if (yes_orientation && T_straight && !I_straight && !M_straight && !R_straight && !P_straight) {
    return G_YES;
  }

  // NO: thumb straight + Index/Middle/Ring/Pinky bent + gyro orientation
  if (no_orientation && T_straight && !I_straight && !M_straight && !R_straight && !P_straight) {
    return G_NO;
  }

  // UP + S S S S => question
  if (hp == HAND_UP && T_straight && I_straight && M_straight && R_straight) return G_QUESTION;

  // Thumb & Index straight, others bent
  if (T_straight && I_straight && !M_straight && !R_straight) {
    if (hp == HAND_UP)   return G_INC_AC;
    if (hp == HAND_DOWN) return G_DEC_AC;
  }

  // Thumb & Index & Middle straight, Ring bent
  if (T_straight && I_straight && M_straight && !R_straight) {
    if (hp == HAND_UP)   return G_INC_LIGHT;
    if (hp == HAND_DOWN) return G_DEC_LIGHT;
  }

  return G_NONE;
}

static inline void updateFingerDebounced(int i, bool candidateBent) {
  if (candidateBent == flexBent[i]) { flexChangeCount[i] = 0; return; }
  if (flexChangeCount[i] < 255) flexChangeCount[i]++;
  if (flexChangeCount[i] >= FLEX_STABLE_N) {
    flexBent[i] = candidateBent;
    flexChangeCount[i] = 0;
  }
}

static inline void updateGestureDebounced(uint8_t candidate) {
  if (candidate == g_lastCandidate) {
    if (g_candidateCount < 255) g_candidateCount++;
  } else {
    g_lastCandidate = candidate;
    g_candidateCount = 1;
  }
  if (g_candidateCount >= GESTURE_STABLE_N) g_stableGesture = candidate;
}

// ═══════════════════════════════════════════════════════════════
//  FLEX & GSR TASK (CORE 0)
// ═══════════════════════════════════════════════════════════════
void flexTask(void *pv) {
  (void)pv;

  const TickType_t period = pdMS_TO_TICKS(80);
  TickType_t lastWake = xTaskGetTickCount();

  int rawLocal[5];
  int kalLocal[5];
  int diffLocal[5];
  int gsrLocal = 0;

  // Auto-calibration: track observed min/max of Kalman values per finger.
  // As the user bends their fingers, the ESP32 learns the full range
  // and detects bent/straight using the midpoint — works in both directions.
  int autoMin[5], autoMax[5];
  for (int i = 0; i < 5; i++) {
    autoMin[i] = flexBaseline[i];
    autoMax[i] = flexBaseline[i];
  }
  int warmup = 25;  // skip first ~2s for Kalman convergence

  while (true) {
    vTaskDelayUntil(&lastWake, period);

    // Read ADC and apply Kalman outside critical section
    for (int i = 0; i < 5; i++) {
      rawLocal[i]  = readFlex(i);
      kalLocal[i]  = (int)roundf(flex[i].kf.update((float)rawLocal[i]));
      diffLocal[i] = kalLocal[i] - flexBaseline[i];
    }

    // Update auto-cal min/max after Kalman has converged
    if (warmup > 0) {
      warmup--;
      // Re-seed auto range from converged Kalman on last warmup tick
      if (warmup == 0) {
        for (int i = 0; i < 5; i++) {
          autoMin[i] = kalLocal[i];
          autoMax[i] = kalLocal[i];
        }
      }
    } else {
      for (int i = 0; i < 5; i++) {
        if (kalLocal[i] < autoMin[i]) autoMin[i] = kalLocal[i];
        if (kalLocal[i] > autoMax[i]) autoMax[i] = kalLocal[i];
      }
    }

    // Read Grove GSR (ADC) in same task (10 samples)
    gsrLocal = readAveraged(GSR_PIN, 10);

    // Update shared flex + gesture + gsr state atomically
    portENTER_CRITICAL(&g_flexMux);

    for (int i = 0; i < 5; i++) {
      flexRaw[i]  = rawLocal[i];
      flexKal[i]  = kalLocal[i];
      flexDiff[i] = diffLocal[i];

      bool candidateBent;
      if (flexHasDashCal) {
        // Priority 1: Dashboard calibration (SETCAL from Processing)
        candidateBent = (flexBendDir[i] < 0)
          ? (kalLocal[i] < flexMidpoint[i])
          : (kalLocal[i] > flexMidpoint[i]);
      } else {
        // Priority 2: Auto-cal from observed min/max range
        int rangeDown = autoMin[i] < flexBaseline[i] ? flexBaseline[i] - autoMin[i] : 0;
        int rangeUp   = autoMax[i] > flexBaseline[i] ? autoMax[i] - flexBaseline[i] : 0;
        if (rangeDown > 50 && rangeDown >= rangeUp) {
          candidateBent = (kalLocal[i] < (flexBaseline[i] + autoMin[i]) / 2);
        } else if (rangeUp > 50 && rangeUp > rangeDown) {
          candidateBent = (kalLocal[i] > (flexBaseline[i] + autoMax[i]) / 2);
        } else {
          // Not enough range yet — bidirectional fallback (works if readings rise OR drop)
          candidateBent = (diffLocal[i] < -60) || (diffLocal[i] > 60);
        }
      }
      updateFingerDebounced(i, candidateBent);
    }

    g_gsrRaw = gsrLocal;

    // Use our global mapping: Ring=0, Thumb=1, Middle=2, Pinky=3, Index=4
    bool T_straight = !flexBent[1];
    bool I_straight = !flexBent[4];
    bool M_straight = !flexBent[2];
    bool R_straight = !flexBent[0];
    bool P_straight = !flexBent[3];

    HandPos hp = g_handPos;
    uint8_t cand = gestureIdFromState(hp, T_straight, I_straight, M_straight, R_straight, P_straight);
    updateGestureDebounced(cand);

    portEXIT_CRITICAL(&g_flexMux);
  }
}
