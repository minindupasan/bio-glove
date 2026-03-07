#pragma once

// ═══════════════════════════════════════════════════════════════
//  KALMAN FILTER  (header-only)
// ═══════════════════════════════════════════════════════════════
struct Kalman {
  float Q, R, P, x;

  Kalman(float q = 2.0f, float r = 25.0f) : Q(q), R(r), P(1.0f), x(0.0f) {}

  inline float update(float z) {
    P += Q;
    float K = P / (P + R);
    x += K * (z - x);
    P *= (1.0f - K);
    return x;
  }

  inline void seed(float v) { x = v; }
};
