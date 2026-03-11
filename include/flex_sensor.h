#pragma once

#include <Arduino.h>
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

// ═══════════════════════════════════════════════════════════════
//  GESTURES & HAND STATE
// ═══════════════════════════════════════════════════════════════
enum HandPos : uint8_t { HAND_UP = 0, HAND_DOWN = 1, HAND_REST = 2 };
extern volatile HandPos g_handPos;

extern portMUX_TYPE g_flexMux;

static const uint8_t G_NONE      = 0;
static const uint8_t G_QUESTION  = 1;
static const uint8_t G_YES       = 2;
static const uint8_t G_NO        = 3;
static const uint8_t G_INC_AC    = 4;
static const uint8_t G_DEC_AC    = 5;
static const uint8_t G_INC_LIGHT = 6;
static const uint8_t G_DEC_LIGHT = 7;

extern int  flexBaseline[5];
extern int  flexRaw[5];
extern int  flexKal[5];
extern int  flexDiff[5];
extern bool flexBent[5];
extern uint8_t flexChangeCount[5];

extern uint8_t g_stableGesture;
extern volatile int g_gsrRaw;

extern int  FLEX_TH_FINGER[5];
extern int  flexMidpoint[5];
extern int  flexBendDir[5];
extern bool flexHasDashCal;

void calibrateFlexSensors();
void applyDashboardCal(const char* payload);
void flexTask(void *pv);
const char* gestureToText(uint8_t g);
