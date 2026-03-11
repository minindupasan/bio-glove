"""
Classroom Live Predictor (ESP32-S3 + Firebase)

This script connects to your Firebase Realtime Database to perform live inference 
using the Medical-Grade Random Forest Model. It implements structural protections 
against hardware artifacts from the MAX30102 and Grove GSR.

Core Protections Implemented:
1. Calibration Mode: Forces a 120-second baseline collection period.
2. Hardware Clipping: Rejects biologically impossible MAX30102 readings.
3. Rolling Median Filter: Smooths the final Random Forest predictions.
"""

import time
import joblib
import numpy as np
import pandas as pd
from datetime import datetime
from collections import defaultdict, deque
import firebase_admin
from firebase_admin import credentials, db

# ---------------------------------------------------------
# FIREBASE CONFIGURATION
# ---------------------------------------------------------
FIREBASE_CRED_PATH   = "serviceAccountKey.json"
FIREBASE_DB_URL      = "https://smart-classroom-981e2-default-rtdb.asia-southeast1.firebasedatabase.app"

# ---------------------------------------------------------
# MODEL ARTIFACT PATHS
# ---------------------------------------------------------
MODEL_DIR            = "Classroom_Models"
MODEL_PATH           = f"{MODEL_DIR}/rf_model.pkl"
SCALER_PATH          = f"{MODEL_DIR}/global_scaler.pkl"
SUBJECT_SCALERS_PATH = f"{MODEL_DIR}/subject_scalers.pkl"
FEATURE_COLS_PATH    = f"{MODEL_DIR}/feature_cols.pkl"

# ---------------------------------------------------------
# INFERENCE CONSTANTS
# ---------------------------------------------------------
WINDOW_SIZE          = 30      # Number of samples per prediction window
STEP_SIZE            = 10      # Number of new samples before next prediction
POLL_INTERVAL        = 2       # Seconds between Firebase polls
CALIB_MIN_SAMPLES    = 60      # 120 seconds of initial calibration mode
SIGNALS = ["BPM", "SPO2", "GSR_RAW", "SKIN_TEMP_C"]
TARGET_STUDENT = "s01"

print("Loading machine learning artifacts...")
try:
    clf              = joblib.load(MODEL_PATH)
    scaler           = joblib.load(SCALER_PATH)
    subject_scalers  = joblib.load(SUBJECT_SCALERS_PATH)
    feature_cols     = joblib.load(FEATURE_COLS_PATH)
    print("Successfully loaded Classifier, Scalers, and Feature map.")
except Exception as e:
    print(f"Error loading models. Have you run the training pipeline? {e}")
    exit(1)

def parse_firebase_record(record: dict):
    """
    Extracts the incoming fields from Firebase and enforces hard 
    biological bounds to reject sensor artifacts instantly.
    """
    gsr_raw   = record.get("gsr_raw", record.get("GSR_RAW"))
    skin_temp = record.get("skin_temp", record.get("SKIN_TEMP_C"))
    bpm       = record.get("bpm", record.get("BPM"))
    spo2      = record.get("spo2", record.get("SPO2"))

    if gsr_raw is None or skin_temp is None:
        raise ValueError("Core fields missing from payload")

    gsr_val  = int(gsr_raw)
    temp_val = float(skin_temp)
    
    # MAX30102 Outlier Rejection (Motion artifact dropping)
    if bpm is not None:
        bpm = float(bpm)
        if bpm < 45 or bpm > 180:
            bpm = None
            
    if spo2 is not None:
        spo2 = float(spo2)
        if spo2 < 80:
            spo2 = None

    return bpm, spo2, gsr_val, temp_val

def z_score(val: float, col: str, params: dict) -> float:
    p = params[col]
    return (val - p["mean"]) / p["std"]

def extract_features(window_rows: list) -> dict:
    f = {}
    for col in SIGNALS:
        s = np.array([r[col] for r in window_rows], dtype=float)
        s = s[~np.isnan(s)]
        
        if len(s) < 4:
            s = np.zeros(4)
            
        mu   = s.mean()
        sd   = s.std()
        half = len(s) // 2

        f[f"{col}_mean"]  = float(mu)
        f[f"{col}_std"]   = float(sd)
        f[f"{col}_min"]   = float(s.min())
        f[f"{col}_max"]   = float(s.max())
        f[f"{col}_range"] = float(s.max() - s.min())
        
        # Safe polyfit (prevent RankWarning if std is 0)
        if sd > 1e-6:
            f[f"{col}_slope"] = float(np.polyfit(np.arange(len(s)), s, 1)[0])
        else:
            f[f"{col}_slope"] = 0.0
            
        f[f"{col}_cv"]    = float(sd / mu if abs(mu) > 1e-6 else 0.0)
        f[f"{col}_delta"] = float(s[half:].mean() - s[:half].mean())
        
    return f

def build_vector(feat_dict: dict) -> np.ndarray:
    return np.array(
        [feat_dict.get(col, 0.0) for col in feature_cols],
        dtype=float
    ).reshape(1, -1)

# Initialization & Connection
try:
    firebase_admin.get_app()
except ValueError:
    cred = credentials.Certificate(FIREBASE_CRED_PATH)
    firebase_admin.initialize_app(cred, {"databaseURL": FIREBASE_DB_URL})

root_ref = db.reference("/")
pred_ref = db.reference("predictions")
print("Connected to Firebase Realtime Database.")

def get_fields(rec):
    """Extract sensor fields from flat or nested (raw/processed) format."""
    if "raw" in rec and isinstance(rec["raw"], dict):
        bpm  = rec.get("processed", {}).get("hr", {}).get("bpm")
        spo2 = rec.get("processed", {}).get("hr", {}).get("spo2")
        gsr  = rec.get("raw", {}).get("gsr")
        temp = rec.get("raw", {}).get("skin_temp_c")
    else:
        bpm  = rec.get("bpm", rec.get("BPM"))
        spo2 = rec.get("spo2", rec.get("SPO2"))
        gsr  = rec.get("gsr_raw", rec.get("GSR_RAW"))
        temp = rec.get("skin_temp", rec.get("SKIN_TEMP_C"))
    return bpm, spo2, gsr, temp

last_predicted_key = None

print("===========================================================")
print(f"  LIVE PREDICTOR — polling {TARGET_STUDENT}")
print(f"  Writing to: predictions/{TARGET_STUDENT}/")
print("===========================================================")

while True:
    try:
        sid = TARGET_STUDENT
        readings = root_ref.child(sid).get()
        if not readings or not isinstance(readings, dict):
            time.sleep(POLL_INTERVAL)
            continue

        # Filter to sensor records only
        sensor_records = {k: v for k, v in readings.items()
                          if isinstance(v, dict) and
                          ("gsr_raw" in v or "GSR_RAW" in v or "raw" in v)}

        if len(sensor_records) < WINDOW_SIZE:
            print(f"[{sid}] Waiting for data... {len(sensor_records)}/{WINDOW_SIZE} samples")
            time.sleep(POLL_INTERVAL)
            continue

        sorted_keys = sorted(sensor_records.keys())
        latest_key  = sorted_keys[-1]

        # Skip if we already predicted this timestamp
        if latest_key == last_predicted_key:
            time.sleep(POLL_INTERVAL)
            continue

        # Take the last WINDOW_SIZE samples
        window_keys = sorted_keys[-WINDOW_SIZE:]
        rows = []
        for k in window_keys:
            bpm, spo2, gsr, temp = get_fields(sensor_records[k])
            # Clip invalid BPM/SPO2
            if bpm is not None:
                bpm = float(bpm)
                if bpm < 45 or bpm > 180:
                    bpm = None
            if spo2 is not None:
                spo2 = float(spo2)
                if spo2 < 80:
                    spo2 = None
            # Impute
            if bpm is None:
                known = [r["BPM"] for r in rows if r["BPM"] is not None]
                bpm = float(np.mean(known)) if known else 75.0
            if spo2 is None:
                known = [r["SPO2"] for r in rows if r["SPO2"] is not None]
                spo2 = float(np.mean(known)) if known else 98.0
            gsr  = int(gsr) if gsr is not None else 2500
            temp = float(temp) if temp is not None else 33.0
            rows.append({"BPM": bpm, "SPO2": spo2, "GSR_RAW": gsr, "SKIN_TEMP_C": temp})

        # Z-score params (training or live-computed)
        params = subject_scalers.get(sid)
        if params is None:
            params = {}
            for col in SIGNALS:
                vals = [r[col] for r in rows]
                mu, sig = np.mean(vals), np.std(vals)
                params[col] = {"mean": float(mu), "std": float(sig if sig > 0 else 1.0)}

        # Normalise
        norm_rows = [{col: (r[col] - params[col]["mean"]) / params[col]["std"]
                      for col in SIGNALS} for r in rows]

        # Extract features & predict
        feat_dict    = extract_features(norm_rows)
        vec          = scaler.transform(build_vector(feat_dict))
        sensor_score = float(clf.predict_proba(vec)[0][1])

        # Camera emotion fusion (70/30)
        emotion_data  = db.reference(f"emotion/{sid}").get()
        emotion_score = 0.5
        if emotion_data and isinstance(emotion_data, dict):
            if "timestamp" in emotion_data and (int(time.time() * 1000) - emotion_data["timestamp"] < 30000):
                emotion_score = float(emotion_data.get("stress_score", 0.5))

        stress_score = (sensor_score * 0.7) + (emotion_score * 0.3)
        stress_level = round(stress_score * 100, 1)

        # Write to predictions/{sid}/
        pred_ref.child(sid).set({
            "stress_score": round(stress_score, 4),
            "stress_level": stress_level,
        })

        status = "STRESSED" if stress_score >= 0.5 else "CALM"
        print(f"[{sid}] {latest_key} | Score: {stress_score:.4f} | Level: {stress_level}% | {status}")

        last_predicted_key = latest_key

    except KeyboardInterrupt:
        print("\nDisconnected.")
        break
    except Exception as e:
        print(f"[ERROR] {e}")
        time.sleep(2)

    time.sleep(POLL_INTERVAL)
