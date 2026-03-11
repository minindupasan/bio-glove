# Bio-Glove Unified Monitor

Real-time student monitoring system combining sensor-based stress prediction, facial emotion analysis, MediaPipe engagement tracking, and sign language detection — all fused and pushed to Firebase.

## Prerequisites

- Python 3.9+
- Webcam
- Firebase service account key (`serviceAccountKey.json`) in this directory
- Sign detection models in `../sign detection/models/` (encoder, prototypes, label encoder)
- Classifier models in `Classroom_Models/` (rf_model, scalers, feature_cols)

## Setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Run

```bash
python3 unified_monitor.py
```

Press **Q** to quit, **R** to reset the sign detection buffer.

## Configuration

Edit the constants at the top of `unified_monitor.py`:

| Variable | Default | Description |
|---|---|---|
| `STUDENT_ID` | `s01` | Student identifier for Firebase paths |
| `FIREBASE_DB_URL` | https://smart-classroom-981e2-default-rtdb.asia-southeast1.firebasedatabase.app/ | Firebase Realtime Database URL |
| `POLL_INTERVAL` | `2` | Sensor polling interval (seconds) |
| `EAR_THRESHOLD` | `0.21` | Eye aspect ratio threshold for drowsiness |
| `YAW_THRESHOLD` | `20` | Head yaw threshold for looking away |
| `PITCH_THRESHOLD` | `15` | Head pitch threshold for looking up/down |
