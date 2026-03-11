// =============================================================
//  ARDUINO UNO — I2C SLAVE PWM CONTROLLER
//
//  Receives brightness value (0–255) from ESP32 via I2C
//  and outputs a 5V PWM signal on pin 9.
//
//  I2C Slave Address: 0x08
//
//  WIRING:
//  Arduino A4 (SDA) ← ESP32 GPIO21 (SDA)  [via level shifter]
//  Arduino A5 (SCL) ← ESP32 GPIO22 (SCL)  [via level shifter]
//  Common GND between both boards
//
//  PWM OUTPUT:
//  Arduino Pin 9 → 220Ω → LED(+) → LED(−) → GND
//  (or connect to MOSFET gate for high-power LED strip)
//
//  NOTE: Use a 3.3V ↔ 5V level shifter on SDA/SCL lines
//        since ESP32 is 3.3V and Arduino Uno is 5V.
// =============================================================

#include <Wire.h>

#define I2C_SLAVE_ADDR  0x08   // Must match ESP32's target address
#define PWM_PIN         9      // Timer1 PWM pin (490 Hz)

volatile uint8_t pwmValue = 0; // Updated by I2C interrupt

// ─────────────────────────────────────────────────────────────
//  I2C RECEIVE HANDLER (called by interrupt)
// ─────────────────────────────────────────────────────────────
void receiveEvent(int numBytes) {
  while (Wire.available()) {
    pwmValue = Wire.read();    // Read brightness byte (0–255)
  }
}

// ─────────────────────────────────────────────────────────────
//  SETUP
// ─────────────────────────────────────────────────────────────
void setup() {
  Serial.begin(115200);
  Serial.println("Arduino PWM Slave v1.0");
  Serial.println("I2C Address: 0x08");
  Serial.println("PWM Output: Pin 9");

  pinMode(PWM_PIN, OUTPUT);
  analogWrite(PWM_PIN, 0);     // Start with LED off

  Wire.begin(I2C_SLAVE_ADDR);  // Join I2C bus as slave
  Wire.onReceive(receiveEvent); // Register receive handler

  Serial.println("Ready — waiting for brightness commands...\n");
}

// ─────────────────────────────────────────────────────────────
//  LOOP — apply PWM value received via I2C
// ─────────────────────────────────────────────────────────────
void loop() {
  static uint8_t lastPWM = 0;

  // Only update and print when value changes
  if (pwmValue != lastPWM) {
    analogWrite(PWM_PIN, pwmValue);
    Serial.print("PWM → ");
    Serial.print(pwmValue);
    Serial.print("/255 (");
    Serial.print((pwmValue * 100) / 255);
    Serial.println("%)");
    lastPWM = pwmValue;
  }

  delay(50);  // Small delay to avoid busy-looping
}
