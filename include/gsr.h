#pragma once

#include "kalman.h"

// ═══════════════════════════════════════════════════════════════
//  GSR
// ═══════════════════════════════════════════════════════════════
#define GSR_PIN  5

extern int    gsrBaseline;
extern Kalman gsrKF;

void gsrCalibrate();
