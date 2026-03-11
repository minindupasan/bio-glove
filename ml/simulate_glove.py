"""
Classroom Glove Simulator (Validation Streamer)

Reads a hold-out test student from Classroom_Dataset and streams 
their data to Firebase at 1-second intervals, perfectly mimicking 
an ESP32-S3 connected to a Grove GSR and MAX30102.
"""

import time
import pandas as pd
import firebase_admin
from firebase_admin import credentials, db
import sys

# ---------------------------------------------------------
# FIREBASE CONFIGURATION
# ---------------------------------------------------------
FIREBASE_CRED_PATH = "serviceAccountKey.json"
FIREBASE_DB_URL    = "https://smart-classroom-981e2-default-rtdb.asia-southeast1.firebasedatabase.app"

# We use S07 as our validation student (they were in the Test split in train_classroom_model.py)
STUDENT_ID = "S05"
DATA_FILE  = f"Classroom_Dataset/{STUDENT_ID}_dataset.csv"

def stream_simulated_glove():
    print("=" * 50)
    print(f"  Glove Simulator — Streaming {STUDENT_ID} to Firebase")
    print("=" * 50)

    try:
        firebase_admin.get_app()
    except ValueError:
        cred = credentials.Certificate(FIREBASE_CRED_PATH)
        firebase_admin.initialize_app(cred, {"databaseURL": FIREBASE_DB_URL})

    root_ref = db.reference("smartglove")
    
    try:
        df = pd.read_csv(DATA_FILE)
    except FileNotFoundError:
        print(f"Error: Could not find {DATA_FILE}. Did you run generate_classroom_data.py?")
        sys.exit(1)

    print(f"\nConnected to Firebase. Streaming {len(df)} 1-second samples...")
    print("Run `python Classroom_Live_Predictor.py` in another terminal to watch the ML model predict this live.\n")

    for i, row in df.iterrows():
        # Mimic exact Firebase key structures from the ESP32
        ts_key = str(int(time.time() * 1000))  # milliseconds timestamp 
        
        payload = {
            "bpm":       None if pd.isna(row["BPM"]) else round(row["BPM"], 1),
            "spo2":      None if pd.isna(row["SPO2"]) else round(row["SPO2"], 1),
            "gsr_raw":   int(row["GSR_RAW"]),
            "skin_temp": round(row["SKIN_TEMP_C"], 2)
        }
        
        # Stream to Firebase
        root_ref.child(f"{STUDENT_ID}/{ts_key}").set(payload)
        
        # Pretty print local status
        b_str = "N/A" if payload["bpm"] is None else f"{payload['bpm']}"
        print(f"[{STUDENT_ID}] Uploaded: BPM={b_str:4}  GSR={payload['gsr_raw']:4}  Temp={payload['skin_temp']}°C  (True Label: {int(row['label'])})")
        
        time.sleep(1)  # Physical delay matching ESP32 hardware polling rate

if __name__ == "__main__":
    stream_simulated_glove()
