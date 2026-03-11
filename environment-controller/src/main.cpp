// =============================================================
//  CLASSROOM ADAPTIVE LIGHT + AC CONTROLLER  v6.0
//
//  DATA SOURCE: Firebase  stress/{sid}/stress_level  (0–100)
//  Written by unified_monitor.py stress fusion loop.
//
//  FIREBASE PATHS READ:
//    /stress/s01/stress_level
//    /stress/s02/stress_level
//    /stress/s03/stress_level
//    /stress/s04/stress_level
//    /stress/s05/stress_level
//
//  FIREBASE PATHS WRITTEN:
//    /environment/s01/  { ac, lighting, ac_setpoint, brightness,
//                         composite_stress, temp, humidity }
//    (same data repeated for s02–s05 so each student's dashboard
//     can read environment)
//
//  ALGORITHMS:
//  [1] Jain's Fairness Index    — equity-weighted classroom stress
//  [2] Gini Coefficient         — inequality penalty
//  [3] Shannon Stress Entropy   — hotspot confidence
//  [4] Type-1 Fuzzy + Centroid  — brightness decision
//  [5] Spatial Zone Analysis    — zone-aware brightness trim
//
//  ENVIRONMENTAL OUTPUTS:
//   LED → I2C to Arduino Uno (slave 0x08) → 5V PWM
//   AC  → IR: power on/off + temperature setpoint on GPIO19
//
//  Hardware: ESP32 | LDR(GPIO34) · DHT22(GPIO35) · IR-TX(GPIO19)
//            I2C Master: SDA(GPIO21) SCL(GPIO22) → Arduino Uno
//
//  Arduino Uno (I2C Slave):
//            Receives brightness 0–255 via I2C
//            Outputs 5V PWM on pin 9
// =============================================================
//
//  WIRING:
//  LDR   leg1 → 3.3V | leg2 → GPIO34 + 1kΩ to GND
//  DHT22 DATA → GPIO32 + 10kΩ pull-up to 3.3V
//  IR-TX GPIO19 → 1kΩ → BC547 Base
//        BC547 Collector → IR LED Cathode
//        IR LED Anode → 3.3V | BC547 Emitter → GND
//
//  I2C (ESP32 → Arduino):
//  ESP32 GPIO21 (SDA) → Arduino A4 (SDA)  [+ level shifter]
//  ESP32 GPIO22 (SCL) → Arduino A5 (SCL)  [+ level shifter]
//  Common GND between both boards
//
//  LIBRARIES:
//  ArduinoJson · DHT sensor library · Adafruit Unified Sensor
//  IRremoteESP8266 · Firebase ESP32 Client (Mobizt v4.x+) · Wire
// =============================================================

#include <Arduino.h>
#include <ArduinoJson.h>
#include <WiFi.h>
#include <FirebaseESP32.h>
#include <DHT.h>
#include <IRremoteESP8266.h>
#include <IRsend.h>
#include <Wire.h>
#include <math.h>

// ── WiFi & Firebase ───────────────────────────────────────────
#define WIFI_SSID     "Dialog 4G 875"
#define WIFI_PASSWORD "f45902DF"
#define FIREBASE_HOST "smart-classroom-981e2-default-rtdb.asia-southeast1.firebasedatabase.app"
#define FIREBASE_AUTH "mYcjkCxN949mjqC8qbJLeZdO8Y3Iby6DwLTCeLXD"

FirebaseData   fbdo;
FirebaseConfig fbConfig;
FirebaseAuth   fbAuth;

// ── Hardware Pins ─────────────────────────────────────────────
#define LDR_PIN        34
#define DHT_PIN        32
#define IR_TX_PIN      19
#define I2C_SDA        21
#define I2C_SCL        22
#define DHT_TYPE   DHT22

// I2C slave address for Arduino PWM controller
#define ARDUINO_I2C_ADDR  0x08

DHT    dht(DHT_PIN, DHT_TYPE);
IRsend irSend(IR_TX_PIN);

// ── AC IR Codes (NEC 32-bit) ──────────────────────────────────
#define AC_CODE_ON        0x00FF02FD
#define AC_CODE_OFF       0x00FF827D
#define AC_CODE_TEMP_UP   0x00FFC23D
#define AC_CODE_TEMP_DOWN 0x00FFE21D
#define AC_CODE_MODE_COOL 0x00FFA25D
#define IR_REPEAT_COUNT   3

// ── AC Temperature Thresholds ─────────────────────────────────
#define AC_TEMP_MIN    18
#define AC_TEMP_MAX    30
#define AC_COMFORT_LOW 22
#define AC_COMFORT_HI  27
#define AC_HYSTERESIS   1.0f

// ── Students ──────────────────────────────────────────────────
#define NUM_STUDENTS 5
#define NUM_ZONES    3

// Firebase paths — read from stress/{sid}/stress_level
const char* FB_PATHS[NUM_STUDENTS] = {
  "/stress/s01/stress_level",
  "/stress/s02/stress_level",
  "/stress/s03/stress_level",
  "/stress/s04/stress_level",
  "/stress/s05/stress_level"
};

const char* STUDENT_NAMES[NUM_STUDENTS] = { "s01","s02","s03","s04","s05" };

// Seating zones — edit to match your classroom layout
// 0 = front row   1 = middle row   2 = back row
const uint8_t STUDENT_ZONE[NUM_STUDENTS] = { 0, 0, 1, 1, 2 };

const char* const ZONE_NAMES[]       = { "front","middle","back" };
const float       ZONE_SENSITIVITY[] = { 1.3f, 1.0f, 0.8f };

// Live stress scores — refreshed from Firebase every loop
float stressScores[NUM_STUDENTS] = { 50, 50, 50, 50, 50 };

// ── AC State ──────────────────────────────────────────────────
struct ACState { bool powerOn; int setpointC; bool irJustSent; };
ACState acState = { false, 26, false };

// ─────────────────────────────────────────────────────────────
//  RESULT STRUCTS
// ─────────────────────────────────────────────────────────────
struct JainResult    { float jfi;  float classroomStress; };
struct GiniResult    { float gini; float penalty; };
struct EntropyResult { float H;    float Hnorm; float confidence; };
struct FuzzyResult   { float muL,  muM, muH, centroid; int brightness; };
struct ZoneResult    {
  float stress[NUM_ZONES];
  float weighted[NUM_ZONES];
  int   count[NUM_ZONES];
  int   hotZone;
  float spatialAdj;
};

// ─────────────────────────────────────────────────────────────
//  ALGORITHM 1 — JAIN'S FAIRNESS INDEX
// ─────────────────────────────────────────────────────────────
JainResult computeJain(float avg) {
  float sumX = 0, sumX2 = 0;
  for (int i = 0; i < NUM_STUDENTS; i++) {
    sumX  += stressScores[i];
    sumX2 += stressScores[i] * stressScores[i];
  }
  float jfi = (sumX2 == 0) ? 1.0f
            : (sumX * sumX) / ((float)NUM_STUDENTS * sumX2);
  jfi = constrain(jfi, 0.0f, 1.0f);
  return { jfi, constrain(avg * (2.0f - jfi), 0.0f, 100.0f) };
}

// ─────────────────────────────────────────────────────────────
//  ALGORITHM 2 — GINI COEFFICIENT
// ─────────────────────────────────────────────────────────────
GiniResult computeGini() {
  float s[NUM_STUDENTS];
  for (int i = 0; i < NUM_STUDENTS; i++) s[i] = stressScores[i];
  for (int i = 0; i < NUM_STUDENTS-1; i++)
    for (int j = 0; j < NUM_STUDENTS-i-1; j++)
      if (s[j] > s[j+1]) { float t=s[j]; s[j]=s[j+1]; s[j+1]=t; }

  float sumX = 0, rankSum = 0;
  for (int i = 0; i < NUM_STUDENTS; i++) {
    sumX    += s[i];
    rankSum += (float)(i+1) * s[i];
  }
  float gini = (sumX == 0) ? 0
             : (2.0f*rankSum) / ((float)NUM_STUDENTS*sumX)
               - (float)(NUM_STUDENTS+1) / (float)NUM_STUDENTS;
  gini = constrain(gini, 0.0f, 1.0f);
  return { gini, gini * 15.0f };
}

// ─────────────────────────────────────────────────────────────
//  ALGORITHM 3 — SHANNON STRESS ENTROPY
// ─────────────────────────────────────────────────────────────
EntropyResult computeEntropy() {
  float total = 0;
  for (int i = 0; i < NUM_STUDENTS; i++) total += stressScores[i];
  float H = 0;
  if (total > 0)
    for (int i = 0; i < NUM_STUDENTS; i++) {
      float p = stressScores[i] / total;
      if (p > 0) H -= p * log2f(p);
    }
  float hNorm = (log2f(NUM_STUDENTS) > 0)
              ? constrain(H / log2f(NUM_STUDENTS), 0.0f, 1.0f) : 0;
  return { H, hNorm, 1.0f - hNorm };
}

// ─────────────────────────────────────────────────────────────
//  COMPOSITE STRESS
// ─────────────────────────────────────────────────────────────
float computeComposite(const JainResult& j,
                       const GiniResult& g,
                       const EntropyResult& e) {
  float maxS = 0;
  for (int i = 0; i < NUM_STUDENTS; i++)
    if (stressScores[i] > maxS) maxS = stressScores[i];

  float withGini = constrain(j.classroomStress + g.penalty,               0, 100);
  float withEnt  = constrain(j.classroomStress + e.confidence*maxS*0.20f, 0, 100);

  return constrain(0.50f*j.classroomStress
                 + 0.30f*withGini
                 + 0.20f*withEnt,
                   0, 100);
}

// ─────────────────────────────────────────────────────────────
//  ALGORITHM 4 — TYPE-1 FUZZY INFERENCE + CENTROID DEFUZZ
// ─────────────────────────────────────────────────────────────
float fzL(float x){ if(x<=0)return 1; if(x>=45)return 0; return(45-x)/45.0f; }
float fzM(float x){ if(x<=20||x>=80)return 0; if(x>=50&&x<=60)return 1;
                    if(x<50)return(x-20)/30.0f; return(80-x)/20.0f; }
float fzH(float x){ if(x<=55)return 0; if(x>=85)return 1; return(x-55)/30.0f; }

FuzzyResult computeFuzzy(float s) {
  float mL=fzL(s), mM=fzM(s), mH=fzH(s), den=mL+mM+mH;
  float c = (den==0) ? 150.0f : (mL*220+mM*140+mH*200)/den;
  return { mL, mM, mH, c, (int)constrain(c,0,255) };
}

// ─────────────────────────────────────────────────────────────
//  ALGORITHM 5 — SPATIAL ZONE ANALYSIS
// ─────────────────────────────────────────────────────────────
ZoneResult computeZones() {
  float sum[NUM_ZONES]={}; int cnt[NUM_ZONES]={};
  for(int i=0;i<NUM_STUDENTS;i++){
    int z=STUDENT_ZONE[i];
    sum[z]+=stressScores[i]; cnt[z]++;
  }
  ZoneResult r; float hotVal=-1; r.hotZone=0;
  for(int z=0;z<NUM_ZONES;z++){
    r.count[z]    = cnt[z];
    r.stress[z]   = cnt[z] ? sum[z]/cnt[z] : 0;
    r.weighted[z] = r.stress[z]*ZONE_SENSITIVITY[z];
    if(r.weighted[z]>hotVal){ hotVal=r.weighted[z]; r.hotZone=z; }
  }
  float n = r.stress[r.hotZone]/100.0f;
  r.spatialAdj = (r.hotZone==0) ?  n*20.0f
               : (r.hotZone==1) ?  n* 5.0f
               :                  -n*10.0f;
  return r;
}

// ─────────────────────────────────────────────────────────────
//  LDR AMBIENT SCALING (Digital Module)
// ─────────────────────────────────────────────────────────────
//  Digital LDRs output 1 (HIGH) when dark, and 0 (LOW) when light.
//  If room is dark: allow full LED brightness (1.0f multiplier).
//  If room is light: dim LEDs (0.3f multiplier).
int applyLDR(int b, int ldrDigital){
  float f = (ldrDigital == 1) ? 1.0f : 0.3f;
  return constrain((int)(b*f), 0, 255);
}

// ─────────────────────────────────────────────────────────────
//  I2C — SEND BRIGHTNESS TO ARDUINO SLAVE
// ─────────────────────────────────────────────────────────────
//  Sends a single byte (0–255) to Arduino at address 0x08.
//  Arduino will output this as a 5V PWM signal on its pin 9.
// ─────────────────────────────────────────────────────────────
void sendBrightnessToArduino(uint8_t brightness) {
  Wire.beginTransmission(ARDUINO_I2C_ADDR);
  Wire.write(brightness);
  uint8_t err = Wire.endTransmission();
  if (err == 0) {
    Serial.printf("I2C → Arduino: brightness=%d (%.0f%%)\n",
                  brightness, (brightness / 255.0f) * 100);
  } else {
    Serial.printf("I2C ERROR %d — Arduino not responding\n", err);
  }
}

// ─────────────────────────────────────────────────────────────
//  IR SEND HELPER
// ─────────────────────────────────────────────────────────────
void sendIR(uint32_t code, const char* label){
  Serial.printf("IR → %s (0x%08X)\n", label, code);
  for(int i=0;i<IR_REPEAT_COUNT;i++){
    irSend.sendNEC(code, 32);
    delay(40);
  }
}

// ─────────────────────────────────────────────────────────────
//  AC CONTROL — POWER ON/OFF + TEMPERATURE SETPOINT
// ─────────────────────────────────────────────────────────────
void updateAC(float tempC, float composite) {
  acState.irJustSent = false;
  if (isnan(tempC)) { Serial.println("AC: DHT read failed"); return; }

  bool needCooling = (tempC > AC_COMFORT_HI - AC_HYSTERESIS)
                  || (composite > 70 && tempC > AC_COMFORT_LOW + 1.0f);

  if (needCooling && !acState.powerOn) {
    sendIR(AC_CODE_ON, "POWER_ON");
    acState.powerOn = true; acState.irJustSent = true;
    delay(500);
  } else if (!needCooling && acState.powerOn && tempC < AC_COMFORT_LOW) {
    sendIR(AC_CODE_OFF, "POWER_OFF");
    acState.powerOn = false; acState.irJustSent = true;
    return;
  }
  if (!acState.powerOn) return;

  int idealSetpt = (int)roundf(AC_COMFORT_HI - (composite/100.0f)*4.0f);
  idealSetpt = constrain(idealSetpt, AC_TEMP_MIN, AC_TEMP_MAX);

  if (idealSetpt < acState.setpointC) {
    sendIR(AC_CODE_TEMP_DOWN, "TEMP_DOWN");
    acState.setpointC--; acState.irJustSent = true;
  } else if (idealSetpt > acState.setpointC) {
    sendIR(AC_CODE_TEMP_UP, "TEMP_UP");
    acState.setpointC++; acState.irJustSent = true;
  }

  if (tempC > AC_COMFORT_HI) sendIR(AC_CODE_MODE_COOL, "MODE_COOL");
}

// ─────────────────────────────────────────────────────────────
//  PUSH ENVIRONMENT DATA TO FIREBASE (for web dashboard)
// ─────────────────────────────────────────────────────────────
//  Writes to /environment/ so the teacher dashboard can see 
//  AC, lighting status, temp, humidity, and ldr.
// ─────────────────────────────────────────────────────────────
void pushEnvironmentToFirebase(int ldr, int finalBright, float composite,
                               float tempC, float humidity) {
  FirebaseJson envJson;
  envJson.set("ac",               acState.powerOn ? "ON" : "OFF");
  envJson.set("ac_setpoint",      acState.setpointC);
  envJson.set("lighting",         String(String((finalBright * 100) / 255) + "%"));
  envJson.set("brightness_pwm",   finalBright);
  envJson.set("ldr",              ldr);
  envJson.set("composite_stress", roundf(composite * 10) / 10.0f);
  envJson.set("temp",             isnan(tempC) ? 0 : roundf(tempC * 10) / 10.0f);
  envJson.set("humidity",         isnan(humidity) ? 0 : roundf(humidity * 10) / 10.0f);
  envJson.set("timestamp",        (double)millis());

  if (!Firebase.setJSON(fbdo, "/environment", envJson)) {
    Serial.printf("  ENV push to /environment FAILED: %s\n", fbdo.errorReason().c_str());
  } else {
    Serial.println("  ENV → Firebase OK (/environment)");
  }
}

// ─────────────────────────────────────────────────────────────
//  JSON OUTPUT  (Web Serial API compatible)
// ─────────────────────────────────────────────────────────────
void emitJSON(int ldr, float composite,
              const JainResult& jain, const GiniResult& gini,
              const EntropyResult& ent, const FuzzyResult& fz,
              const ZoneResult& zone, int finalBright,
              float tempC, float humidity) {

  StaticJsonDocument<2048> doc;

  doc["composite_stress"] = roundf(composite*10)/10.0f;
  doc["ldr"]              = ldr;
  doc["final_bright"]     = finalBright;
  doc["bright_pct"]       = (finalBright*100)/255;

  JsonObject j1 = doc.createNestedObject("jain");
  j1["jfi"]     = roundf(jain.jfi*1000)/1000.0f;
  j1["cs"]      = roundf(jain.classroomStress*10)/10.0f;

  JsonObject j2 = doc.createNestedObject("gini");
  j2["gini"]    = roundf(gini.gini*1000)/1000.0f;
  j2["penalty"] = roundf(gini.penalty*10)/10.0f;

  JsonObject j3 = doc.createNestedObject("entropy");
  j3["H"]       = roundf(ent.H*1000)/1000.0f;
  j3["Hnorm"]   = roundf(ent.Hnorm*1000)/1000.0f;
  j3["conf"]    = roundf(ent.confidence*1000)/1000.0f;

  JsonObject j4    = doc.createNestedObject("fuzzy");
  j4["muL"]        = roundf(fz.muL*1000)/1000.0f;
  j4["muM"]        = roundf(fz.muM*1000)/1000.0f;
  j4["muH"]        = roundf(fz.muH*1000)/1000.0f;
  j4["centroid"]   = roundf(fz.centroid*10)/10.0f;
  j4["brightness"] = fz.brightness;

  JsonObject j5       = doc.createNestedObject("zones");
  j5["hot_zone"]      = ZONE_NAMES[zone.hotZone];
  j5["spatial_adj"]   = roundf(zone.spatialAdj*10)/10.0f;
  JsonArray za        = j5.createNestedArray("detail");
  for(int z=0;z<NUM_ZONES;z++){
    JsonObject zo  = za.createNestedObject();
    zo["zone"]     = ZONE_NAMES[z];
    zo["count"]    = zone.count[z];
    zo["stress"]   = roundf(zone.stress[z]*10)/10.0f;
    zo["weighted"] = roundf(zone.weighted[z]*10)/10.0f;
  }

  doc["temp"]     = isnan(tempC)    ? -1 : roundf(tempC*10)/10.0f;
  doc["humidity"] = isnan(humidity) ? -1 : roundf(humidity*10)/10.0f;
  doc["ac_on"]    = acState.powerOn;
  doc["ac_set"]   = acState.setpointC;
  doc["ir_sent"]  = acState.irJustSent;

  JsonArray arr = doc.createNestedArray("students");
  for(int i=0;i<NUM_STUDENTS;i++){
    JsonObject obj  = arr.createNestedObject();
    obj["id"]       = STUDENT_NAMES[i];
    obj["zone"]     = ZONE_NAMES[STUDENT_ZONE[i]];
    obj["stress"]   = roundf(stressScores[i]*10)/10.0f;
  }

  Serial.print("DATA:");
  serializeJson(doc, Serial);
  Serial.println();
}

// ─────────────────────────────────────────────────────────────
//  SETUP
// ─────────────────────────────────────────────────────────────
void setup() {
  Serial.begin(115200);
  delay(1000);

  // I2C Master for Arduino communication
  Wire.begin(I2C_SDA, I2C_SCL);

  dht.begin();
  irSend.begin();

  Serial.println("==============================================");
  Serial.println(" Classroom Controller v6.0");
  Serial.println(" Data: stress/{sid}/stress_level");
  Serial.println(" LED: I2C → Arduino (5V PWM)");
  Serial.println("==============================================");

  pinMode(LDR_PIN, INPUT);

  Serial.print("Wi-Fi connecting");
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  while(WiFi.status() != WL_CONNECTED){ Serial.print("."); delay(500); }
  Serial.println("\nWi-Fi OK: " + WiFi.localIP().toString());

  fbConfig.database_url               = FIREBASE_HOST;
  fbConfig.signer.tokens.legacy_token = FIREBASE_AUTH;
  Firebase.reconnectWiFi(true);
  Firebase.begin(&fbConfig, &fbAuth);
  Serial.println("Firebase OK.");
  Serial.println("==============================================\n");
}

// ─────────────────────────────────────────────────────────────
//  MAIN LOOP — every 2 seconds
// ─────────────────────────────────────────────────────────────
void loop() {

  // ── STEP 1 : Fetch all 5 stress_level values from Firebase ───
  Serial.println("--- Reading stress levels from Firebase ---");
  for(int i = 0; i < NUM_STUDENTS; i++){
    if(Firebase.getFloat(fbdo, FB_PATHS[i])){
      stressScores[i] = constrain(fbdo.floatData(), 0.0f, 100.0f);
      Serial.printf("  %-4s  stress_level = %.1f\n",
                    STUDENT_NAMES[i], stressScores[i]);
    } else {
      Serial.printf("  %-4s  ERROR: %s  (keeping %.1f)\n",
                    STUDENT_NAMES[i],
                    fbdo.errorReason().c_str(),
                    stressScores[i]);
    }
  }

  // ── STEP 2 : Plain average ───────────────────────────────────
  float avg = 0;
  for(int i=0; i<NUM_STUDENTS; i++) avg += stressScores[i];
  avg /= NUM_STUDENTS;

  // ── STEP 3 : Run algorithms 1–3 → composite stress ───────────
  JainResult    jain      = computeJain(avg);
  GiniResult    gini      = computeGini();
  EntropyResult ent       = computeEntropy();
  float         composite = computeComposite(jain, gini, ent);

  // ── STEP 4 : Algorithm 4 — Fuzzy → base brightness ───────────
  FuzzyResult fz = computeFuzzy(composite);

  // ── STEP 5 : Algorithm 5 — Zone spatial trim ─────────────────
  ZoneResult zone = computeZones();

  // ── STEP 6 : Read ambient sensors ────────────────────────────
  // Digital LDR: 1 = Dark, 0 = Light
  int   ldr      = digitalRead(LDR_PIN);
  float humidity = dht.readHumidity();
  float tempC    = dht.readTemperature();

  // ── STEP 7 : Compute final LED brightness ─────────────────────
  int ledBright = constrain(
        applyLDR(fz.brightness, ldr) + (int)zone.spatialAdj,
        0, 255);

  // ── STEP 8 : Send brightness to Arduino via I2C ──────────────
  sendBrightnessToArduino((uint8_t)ledBright);

  // ── STEP 9 : Apply AC setpoint ────────────────────────────────
  updateAC(tempC, composite);

  // ── STEP 10 : Push environment data to Firebase ───────────────
  pushEnvironmentToFirebase(ldr, ledBright, composite, tempC, humidity);

  // ── STEP 11 : Human-readable summary ─────────────────────────
  Serial.printf(
    "\n  Avg:%.1f  Composite:%.1f\n"
    "  JFI:%.3f  Gini:%.3f  Entropy:%.3f (spread %.0f%%)\n"
    "  HotZone:%-6s  FuzzyB:%d  ZoneAdj:%+.1f  LED:%d/255 (%d%%)\n"
    "  Temp:%.1f°C  Hum:%.1f%%  AC:%s @ %d°C  IR:%s\n\n",
    avg, composite,
    jain.jfi, gini.gini, ent.H, ent.Hnorm*100,
    ZONE_NAMES[zone.hotZone], fz.brightness, zone.spatialAdj,
    ledBright, (ledBright*100)/255,
    tempC, humidity,
    acState.powerOn ? "ON" : "OFF",
    acState.setpointC,
    acState.irJustSent ? "SENT" : "idle");

  // ── STEP 12 : JSON for Web Serial dashboard ───────────────────
  emitJSON(ldr, composite, jain, gini, ent, fz, zone,
           ledBright, tempC, humidity);

  delay(2000);
}
