#include "flex_sensor.h"
#include <Arduino.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
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

static const int FLEX_TH_FINGER[5] = {
  100, // Ring (index 0)
  100, // Thumb (index 1)
  100, // Middle (index 2)
  100, // Pinky (index 3 - not used in gestures, but defined)
  100  // Index (index 4)
};
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
//  GESTURE DEBOUNCING & MAPPING
// ═══════════════════════════════════════════════════════════════
const char* gestureToText(uint8_t g) {
  switch (g) {
    case G_QUESTION:   return "question";
    case G_YES:        return "understood/yes";
    case G_NO:         return "No (Didn\u2019t Understood)";
    case G_INC_AC:     return "increase AC";
    case G_DEC_AC:     return "decrease AC";
    case G_INC_LIGHT:  return "increase light";
    case G_DEC_LIGHT:  return "decrease light";
    default:           return "-";
  }
}

static uint8_t gestureIdFromState(HandPos hp, bool T_straight, bool I_straight, bool M_straight, bool R_straight) {
  // UP + S S S S => question
  if (hp == HAND_UP && T_straight && I_straight && M_straight && R_straight) return G_QUESTION;

  // Thumb straight, others bent
  if (T_straight && !I_straight && !M_straight && !R_straight) {
    if (hp == HAND_UP)   return G_YES;
    if (hp == HAND_DOWN) return G_NO;
  }

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

  while (true) {
    vTaskDelayUntil(&lastWake, period);

    // Read ADC and apply Kalman outside critical section
    for (int i = 0; i < 5; i++) {
      rawLocal[i]  = readFlex(i);
      kalLocal[i]  = (int)roundf(flex[i].kf.update((float)rawLocal[i]));
      diffLocal[i] = kalLocal[i] - flexBaseline[i];
    }

    // Read Grove GSR (ADC) in same task (10 samples)
    gsrLocal = readAveraged(GSR_PIN, 10);

    // Update shared flex + gesture + gsr state atomically
    portENTER_CRITICAL(&g_flexMux);

    for (int i = 0; i < 5; i++) {
      flexRaw[i]  = rawLocal[i];
      flexKal[i]  = kalLocal[i];
      flexDiff[i] = diffLocal[i];
      
      // Flex readings drop when bent. Strict directional check prevents false positives from resting drift.
      bool candidateBent = (diffLocal[i] < -FLEX_TH_FINGER[i]);
      updateFingerDebounced(i, candidateBent);
    }

    g_gsrRaw = gsrLocal;

    // Use our global mapping: Ring=0, Thumb=1, Middle=2, Pinky=3, Index=4
    bool T_straight = !flexBent[1];
    bool I_straight = !flexBent[4];
    bool M_straight = !flexBent[2];
    bool R_straight = !flexBent[0];

    HandPos hp = g_handPos;
    uint8_t cand = gestureIdFromState(hp, T_straight, I_straight, M_straight, R_straight);
    updateGestureDebounced(cand);

    portEXIT_CRITICAL(&g_flexMux);
  }
}
