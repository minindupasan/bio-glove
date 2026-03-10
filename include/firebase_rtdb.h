#pragma once

// ═══════════════════════════════════════════════════════════════
//  FIREBASE RTDB — WiFi + REST API
// ═══════════════════════════════════════════════════════════════

// Connect to WiFi (blocking, with timeout)
void wifiConnect();

// FreeRTOS task: periodically PATCHes sensor data to Firebase RTDB
void firebaseTask(void *pv);
