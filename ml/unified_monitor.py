"""
Unified Bio-Glove Monitor
Shares one webcam feed for DeepFace emotion + MediaPipe engagement,
runs RF sensor predictor in a background thread, and fuses the two
stress signals (sensor 70% + emotion 30%) into stress/{STUDENT_ID}/.
"""

import os
os.environ["PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION"] = "python"

import cv2
import mediapipe as mp
import numpy as np
import threading
import time
import joblib
from collections import deque
from datetime import datetime

from deepface import DeepFace
import firebase_admin
from firebase_admin import credentials, db

# ---------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------
STUDENT_ID           = "s01"
FIREBASE_CRED_PATH   = "serviceAccountKey.json"
FIREBASE_DB_URL      = "https://smart-classroom-981e2-default-rtdb.asia-southeast1.firebasedatabase.app"

MODEL_DIR            = "Classroom_Models"
MODEL_PATH           = f"{MODEL_DIR}/rf_model.pkl"
SCALER_PATH          = f"{MODEL_DIR}/global_scaler.pkl"
SUBJECT_SCALERS_PATH = f"{MODEL_DIR}/subject_scalers.pkl"
FEATURE_COLS_PATH    = f"{MODEL_DIR}/feature_cols.pkl"

POLL_INTERVAL        = 2
SIGNALS              = ["BPM", "SPO2", "GSR_RAW", "SKIN_TEMP_C"]
FIREBASE_PUSH_INTERVAL = 2

EAR_THRESHOLD        = 0.21
YAW_THRESHOLD        = 20
PITCH_THRESHOLD      = 15
HISTORY_LENGTH       = 20

# ---------------------------------------------------------
# SIGN DETECTION CONSTANTS
# ---------------------------------------------------------
SIGN_MODEL_DIR      = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   "..", "sign detection", "models")
SIGN_SEQUENCE_LEN   = 30
SIGN_TOTAL_FEATURES = 225
SIGN_CONF_THRESHOLD = 0.5
SIGN_PRED_SMOOTHING = 5
NUM_POSE_LM         = 33
NUM_HAND_LM         = 21
FEATURES_PER_LM     = 3

# ---------------------------------------------------------
# FIREBASE INITIALIZATION (once)
# ---------------------------------------------------------
print("Connecting to Firebase...")
try:
    firebase_admin.get_app()
except ValueError:
    cred = credentials.Certificate(FIREBASE_CRED_PATH)
    firebase_admin.initialize_app(cred, {"databaseURL": FIREBASE_DB_URL})

root_ref    = db.reference("smartglove")
emotion_ref = db.reference(f"emotion/{STUDENT_ID}")
eng_ref     = db.reference(f"engagement/{STUDENT_ID}")
pred_ref    = db.reference("predictions")
stress_ref  = db.reference("stress")
sign_ref    = db.reference(f"sign/{STUDENT_ID}")
print("Connected to Firebase Realtime Database.")

# ---------------------------------------------------------
# MODEL LOADING
# ---------------------------------------------------------
print("Loading ML artifacts...")
try:
    clf             = joblib.load(MODEL_PATH)
    scaler          = joblib.load(SCALER_PATH)
    subject_scalers = joblib.load(SUBJECT_SCALERS_PATH)
    feature_cols    = joblib.load(FEATURE_COLS_PATH)
    print("Successfully loaded Classifier, Scalers, and Feature map.")
except Exception as e:
    print(f"Error loading models: {e}")
    raise SystemExit(1)

# ---------------------------------------------------------
# SIGN DETECTION MODEL LOADING (lazy TF import to avoid mp.solutions conflict)
# ---------------------------------------------------------

print("Loading sign detection model...")
try:
    import tensorflow as tf

    @tf.keras.utils.register_keras_serializable()
    class ReduceSumLayer(tf.keras.layers.Layer):
        def call(self, x):
            return tf.reduce_sum(x, axis=1)

    @tf.keras.utils.register_keras_serializable()
    class L2NormalizeLayer(tf.keras.layers.Layer):
        def call(self, x):
            return tf.math.l2_normalize(x, axis=1)

    _encoder_path   = os.path.join(SIGN_MODEL_DIR, "encoder.keras")
    _proto_path     = os.path.join(SIGN_MODEL_DIR, "prototypes.npy")
    _label_path     = os.path.join(SIGN_MODEL_DIR, "label_encoder.npy")
    sign_encoder    = tf.keras.models.load_model(
        _encoder_path, safe_mode=False,
        custom_objects={"ReduceSumLayer": ReduceSumLayer, "L2NormalizeLayer": L2NormalizeLayer}
    )
    sign_prototypes = np.load(_proto_path)
    sign_labels     = np.load(_label_path, allow_pickle=True)
    print(f"Sign model loaded. {len(sign_labels)} sign classes.")
    SIGN_MODEL_AVAILABLE = True
except Exception as e:
    print(f"Sign model not loaded (will be disabled): {e}")
    sign_encoder = sign_prototypes = sign_labels = None
    SIGN_MODEL_AVAILABLE = False

# ---------------------------------------------------------
# SHARED GLOBALS (emotion state, protected by lock)
# ---------------------------------------------------------
emotion_lock        = threading.Lock()
emotion_score       = 0.0   # 0.0–1.0
emotion_label       = "Analyzing..."
last_pushed_emotion = None

# ---------------------------------------------------------
# SHARED GLOBALS (sign detection state)
# ---------------------------------------------------------
sign_lock              = threading.Lock()
sign_label             = None
sign_confidence        = 0.0
sign_top3              = None
sign_inference_running = False

# ---------------------------------------------------------
# SENSOR FEATURE HELPERS (from Classroom_Live_Predictor.py)
# ---------------------------------------------------------

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

# ---------------------------------------------------------
# SIGN DETECTION HELPERS (verbatim from sign detection/realtime.py)
# ---------------------------------------------------------

# Colors (BGR) — from realtime.py
COLOR_TEXT     = (255, 255, 255)
COLOR_ACCENT   = (0, 230, 118)
COLOR_LOW_CONF = (0, 165, 255)
COLOR_POSE     = (80, 110, 200)
COLOR_HAND_L   = (255, 120, 80)
COLOR_HAND_R   = (80, 200, 255)
COLOR_PANEL    = (40, 40, 40)


def extract_landmarks(results):
    """Extract flat feature vector from MediaPipe Holistic results."""
    features = []

    if results.pose_landmarks:
        for lm in results.pose_landmarks.landmark:
            features.extend([lm.x, lm.y, lm.z])
    else:
        features.extend([0.0] * NUM_POSE_LM * FEATURES_PER_LM)

    if results.left_hand_landmarks:
        for lm in results.left_hand_landmarks.landmark:
            features.extend([lm.x, lm.y, lm.z])
    else:
        features.extend([0.0] * NUM_HAND_LM * FEATURES_PER_LM)

    if results.right_hand_landmarks:
        for lm in results.right_hand_landmarks.landmark:
            features.extend([lm.x, lm.y, lm.z])
    else:
        features.extend([0.0] * NUM_HAND_LM * FEATURES_PER_LM)

    return np.array(features, dtype=np.float32)


def draw_landmarks_styled(frame, results):
    """Draw pose and hand landmarks with custom styling."""
    mp_drawing  = mp.solutions.drawing_utils
    mp_holistic = mp.solutions.holistic

    pose_style       = mp_drawing.DrawingSpec(color=COLOR_POSE,   thickness=2, circle_radius=2)
    hand_l_style     = mp_drawing.DrawingSpec(color=COLOR_HAND_L, thickness=2, circle_radius=3)
    hand_r_style     = mp_drawing.DrawingSpec(color=COLOR_HAND_R, thickness=2, circle_radius=3)
    connection_style = mp_drawing.DrawingSpec(color=(100, 100, 100), thickness=1)

    if results.pose_landmarks:
        mp_drawing.draw_landmarks(
            frame, results.pose_landmarks,
            mp_holistic.POSE_CONNECTIONS,
            pose_style, connection_style,
        )
    if results.left_hand_landmarks:
        mp_drawing.draw_landmarks(
            frame, results.left_hand_landmarks,
            mp_holistic.HAND_CONNECTIONS,
            hand_l_style, mp_drawing.DrawingSpec(color=COLOR_HAND_L, thickness=1),
        )
    if results.right_hand_landmarks:
        mp_drawing.draw_landmarks(
            frame, results.right_hand_landmarks,
            mp_holistic.HAND_CONNECTIONS,
            hand_r_style, mp_drawing.DrawingSpec(color=COLOR_HAND_R, thickness=1),
        )


def draw_prediction_panel(frame, prediction, confidence, buffer_fill, top3=None):
    """Draw a styled prediction display panel on the frame."""
    h, w    = frame.shape[:2]
    panel_h = 140
    panel_y = h - panel_h

    overlay = frame.copy()
    cv2.rectangle(overlay, (0, panel_y), (w, h), COLOR_PANEL, -1)
    cv2.addWeighted(overlay, 0.8, frame, 0.2, 0, frame)

    # Buffer progress bar
    bar_x, bar_y = 20, panel_y + 15
    bar_w, bar_h = 200, 12
    fill_w = int(bar_w * buffer_fill)
    cv2.rectangle(frame, (bar_x, bar_y), (bar_x + bar_w, bar_y + bar_h), (80, 80, 80), -1)
    cv2.rectangle(frame, (bar_x, bar_y), (bar_x + fill_w, bar_y + bar_h), COLOR_ACCENT, -1)
    cv2.putText(frame, f"Buffer: {int(buffer_fill * 100)}%",
                (bar_x + bar_w + 10, bar_y + bar_h - 1),
                cv2.FONT_HERSHEY_SIMPLEX, 0.4, COLOR_TEXT, 1)

    if prediction and confidence > 0:
        color      = COLOR_ACCENT if confidence >= SIGN_CONF_THRESHOLD else COLOR_LOW_CONF
        label_text = prediction.upper()

        cv2.putText(frame, label_text,
                    (20, panel_y + 60),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.2, color, 3)

        conf_text = f"Similarity: {confidence:.2f}"
        cv2.putText(frame, conf_text,
                    (20, panel_y + 85),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)

        if top3:
            y_offset = panel_y + 50
            x_offset = w - 300
            cv2.putText(frame, "Top-3:", (x_offset, y_offset),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, COLOR_TEXT, 1)
            for i, (t3_label, t3_conf) in enumerate(top3):
                y_pos   = y_offset + 20 + i * 22
                t3_color = COLOR_ACCENT if i == 0 else (180, 180, 180)
                cv2.putText(frame, f"{i+1}. {t3_label} ({t3_conf:.2f})",
                            (x_offset, y_pos),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.45, t3_color, 1)
    else:
        cv2.putText(frame, "Waiting for sign...",
                    (20, panel_y + 70),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (150, 150, 150), 2)

    cv2.putText(frame, "Q: Quit | R: Reset",
                (w - 180, panel_y + 125),
                cv2.FONT_HERSHEY_SIMPLEX, 0.4, (120, 120, 120), 1)


def run_sign_inference(buffer_snapshot):
    """Background thread: embed 30-frame window → cosine similarity → update globals."""
    global sign_label, sign_confidence, sign_top3, sign_inference_running
    try:
        input_data = np.expand_dims(np.array(buffer_snapshot, dtype=np.float32), 0)
        embedding  = sign_encoder.predict(input_data, verbose=0)[0]
        embedding  = embedding / (np.linalg.norm(embedding) + 1e-8)
        sims       = embedding @ sign_prototypes.T
        top_idx    = np.argsort(sims)[::-1]
        top3       = [(sign_labels[top_idx[i]], sims[top_idx[i]])
                      for i in range(min(3, len(top_idx)))]
        with sign_lock:
            sign_label      = sign_labels[top_idx[0]]
            sign_confidence = sims[top_idx[0]]
            sign_top3       = top3
        print(f"Sign → {sign_labels[top_idx[0]]} ({sims[top_idx[0]]:.2f})")
        # Push sign detection to Firebase for web dashboard
        try:
            sign_ref.set({
                "label": str(sign_labels[top_idx[0]]),
                "confidence": float(sims[top_idx[0]]),
            })
        except Exception as e:
            print(f"Sign Firebase error: {e}")
    except Exception as e:
        print(f"Sign inference error: {e}")
    finally:
        sign_inference_running = False


# ---------------------------------------------------------
# MEDIAPIPE ENGAGEMENT HELPERS (from advanced_engagement.py)
# ---------------------------------------------------------

def calculate_ear(landmarks, refer_idxs, frame_w, frame_h):
    try:
        coords_points = []
        for i in refer_idxs:
            lm = landmarks[i]
            coord = np.array([lm.x * frame_w, lm.y * frame_h])
            coords_points.append(coord)
        v1  = np.linalg.norm(coords_points[1] - coords_points[5])
        v2  = np.linalg.norm(coords_points[2] - coords_points[4])
        h   = np.linalg.norm(coords_points[0] - coords_points[3])
        return (v1 + v2) / (2.0 * h)
    except Exception:
        return 0.0


def get_head_pose(landmarks, frame_w, frame_h):
    face_3d = []
    face_2d = []
    for idx in [1, 199, 33, 263, 61, 291]:
        lm = landmarks[idx]
        x, y = int(lm.x * frame_w), int(lm.y * frame_h)
        face_2d.append([x, y])
        face_3d.append([x, y, lm.z])
    face_2d = np.array(face_2d, dtype=np.float64)
    face_3d = np.array(face_3d, dtype=np.float64)
    focal_length = 1 * frame_w
    cam_matrix = np.array([[focal_length, 0, frame_h / 2],
                            [0, focal_length, frame_w / 2],
                            [0, 0, 1]])
    dist_matrix = np.zeros((4, 1), dtype=np.float64)
    success, rot_vec, _ = cv2.solvePnP(face_3d, face_2d, cam_matrix, dist_matrix)
    if not success:
        return 0, 0, 0
    rmat, _ = cv2.Rodrigues(rot_vec)
    angles, *_ = cv2.RQDecomp3x3(rmat)
    return angles[0] * 360, angles[1] * 360, angles[2] * 360


def get_gaze_ratio(landmarks, frame_w, frame_h):
    left_iris  = np.array([landmarks[468].x * frame_w, landmarks[468].y * frame_h])
    eye_inner  = np.array([landmarks[133].x * frame_w, landmarks[133].y * frame_h])
    eye_outer  = np.array([landmarks[33].x  * frame_w, landmarks[33].y  * frame_h])
    d_inner    = np.linalg.norm(left_iris - eye_inner)
    d_outer    = np.linalg.norm(left_iris - eye_outer)
    total      = d_inner + d_outer
    return d_inner / total if total > 0 else 0.5

# ---------------------------------------------------------
# ENGAGEMENT SYSTEM
# ---------------------------------------------------------

class EngagementSystem:
    def __init__(self):
        self.face_mesh = mp.solutions.face_mesh.FaceMesh(
            max_num_faces=1,
            refine_landmarks=True,
            min_detection_confidence=0.5,
            min_tracking_confidence=0.5
        )
        self.engagement_history = deque(maxlen=HISTORY_LENGTH)
        self.last_push_time     = 0
        self.status_text        = "Searching..."
        self.status_color       = (200, 200, 200)
        self.score              = 0.0

    def process(self, frame):
        """Process a single BGR frame. Updates self.status_text/score and throttled Firebase writes."""
        h, w = frame.shape[:2]
        rgb  = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        results = self.face_mesh.process(rgb)

        if results.multi_face_landmarks:
            for face_landmarks in results.multi_face_landmarks:
                lm = face_landmarks.landmark

                pitch, yaw, _ = get_head_pose(lm, w, h)
                gaze_ratio    = get_gaze_ratio(lm, w, h)
                left_ear      = calculate_ear(lm, [362, 385, 387, 263, 373, 380], w, h)
                right_ear     = calculate_ear(lm, [33,  160, 158, 133, 153, 144],  w, h)
                avg_ear       = (left_ear + right_ear) / 2.0

                is_engaged    = True
                status_text   = "ENGAGED"
                status_color  = (0, 255, 0)

                if avg_ear < EAR_THRESHOLD:
                    status_text  = "SLEEPING / NODDING" if pitch < -PITCH_THRESHOLD else "DROWSY / BLINK"
                    status_color = (0, 0, 255)
                    is_engaged   = False
                else:
                    if yaw > YAW_THRESHOLD:
                        status_text, status_color, is_engaged = "Looking RIGHT (Head)", (0, 0, 255), False
                    elif yaw < -YAW_THRESHOLD:
                        status_text, status_color, is_engaged = "Looking LEFT (Head)", (0, 0, 255), False
                    elif abs(yaw) < 15:
                        if pitch > PITCH_THRESHOLD:
                            status_text, status_color, is_engaged = "Looking UP", (0, 0, 255), False
                        elif pitch < -PITCH_THRESHOLD:
                            status_text, status_color, is_engaged = "Looking DOWN / Reading", (0, 140, 255), True

                    if is_engaged:
                        if gaze_ratio > 0.60 or gaze_ratio < 0.40:
                            status_text, status_color, is_engaged = "Looking Away (Eyes)", (0, 140, 255), False

                self.engagement_history.append(1 if is_engaged else 0)
                self.score       = sum(self.engagement_history) / len(self.engagement_history) * 100
                self.status_text  = status_text
                self.status_color = status_color

                now = time.time()
                if now - self.last_push_time >= FIREBASE_PUSH_INTERVAL:
                    try:
                        eng_ref.update({
                            "engagement_score":  round(self.score, 1),
                            "engagement_status": status_text,
                        })
                        self.last_push_time = now
                    except Exception as e:
                        print(f"Engagement Firebase error: {e}")
        else:
            self.status_text  = "Searching..."
            self.status_color = (200, 200, 200)

    def draw(self, frame):
        h, w = frame.shape[:2]
        score_color = (0, 255, 0) if self.score > 50 else (0, 0, 255)

        def outline(text, pos, scale, color, thick=2):
            cv2.putText(frame, text, pos, cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), thick + 3)
            cv2.putText(frame, text, pos, cv2.FONT_HERSHEY_SIMPLEX, scale, color, thick)

        outline(f"ENG: {self.status_text}",  (10, 145), 0.6, self.status_color)
        outline(f"SCORE: {int(self.score)}%", (10, 175), 0.7, score_color)

# ---------------------------------------------------------
# DEEPFACE THREAD FUNCTION (adapted from emotion.py)
# ---------------------------------------------------------

def push_emotion_firebase(label, score):
    try:
        root_ref.child(f"{STUDENT_ID}/camera_emotion").set({
            "emotion":       label,
            "emotion_score": score,
            "timestamp":     int(time.time() * 1000)
        })
        emotion_ref.set({
            "stress_score":  score,
            "emotion_label": label,
            "timestamp":     int(time.time() * 1000)
        })
    except Exception as e:
        print(f"Emotion Firebase error: {e}")


def analyze_face(frame):
    global emotion_score, emotion_label, last_pushed_emotion
    try:
        result = DeepFace.analyze(frame, actions=["emotion"], enforce_detection=False)
        if isinstance(result, list):
            result = result[0]

        dominant = result["dominant_emotion"]

        if dominant in ("angry", "fear", "sad", "disgust"):
            score, stress_text, col = 1.0, "HIGH STRESS",  (0, 0, 255)
        elif dominant in ("neutral", "happy"):
            score, stress_text, col = 0.0, "RELAXED",      (0, 255, 0)
        else:
            score, stress_text, col = 0.5, "MODERATE",     (0, 255, 255)

        with emotion_lock:
            emotion_score  = score
            emotion_label  = dominant
            changed        = (dominant != last_pushed_emotion)
            if changed:
                last_pushed_emotion = dominant

        if changed:
            push_emotion_firebase(dominant, score)
            print(f"Emotion → {dominant} (score: {score})")

    except Exception as e:
        print(f"DeepFace error: {e}")

# ---------------------------------------------------------
# PREDICTOR THREAD (adapted from Classroom_Live_Predictor.py)
# ---------------------------------------------------------

def stress_fusion_loop():
    """Reads predictions/{sid} (sensor) and emotion/{sid} (camera),
       fuses them as stress = sensor*0.7 + emotion*0.3,
       and writes the result to stress/{sid}/."""
    sid              = STUDENT_ID
    last_fused_ts    = 0

    print(f"Stress fusion thread started for {sid}")
    print(f"  → reading predictions/{sid}/ + emotion/{sid}/")
    print(f"  → writing stress/{sid}/")

    while True:
        try:
            # Read sensor stress score from predictions/{sid}/
            pred_data = pred_ref.child(sid).get()
            sensor_score = 0.5  # default if no prediction yet
            if pred_data and isinstance(pred_data, dict):
                sensor_score = float(pred_data.get("stress_score", 0.5))

            # Read emotion stress score from emotion/{sid}/
            emotion_data = db.reference(f"emotion/{sid}").get()
            em_score = 0.0  # default if no emotion yet
            if emotion_data and isinstance(emotion_data, dict):
                em_score = float(emotion_data.get("stress_score", 0.0))

            # Also update the shared emotion global for the UI overlay
            with emotion_lock:
                em_from_camera = emotion_score
            # Use the live camera emotion if available (more real-time),
            # fall back to Firebase emotion data otherwise
            em = em_from_camera

            # Fuse: stress = sensor*0.7 + emotion*0.3
            fused_score  = (sensor_score * 0.7) + (em * 0.3)
            stress_level = round(fused_score * 100, 1)
            status       = "STRESSED" if fused_score >= 0.5 else "CALM"

            # Write fused score to stress/{sid}/
            stress_ref.child(sid).set({
                "stress_score":  round(fused_score, 4),
                "stress_level":  stress_level,
                "sensor_score":  round(sensor_score, 4),
                "emotion_score": round(em, 4),
                "status":        status,
                "timestamp":     int(time.time() * 1000),
            })

            print(f"[stress/{sid}] fused={fused_score:.4f} ({status}) "
                  f"sensor={sensor_score:.3f} emotion={em:.3f}")

        except Exception as e:
            print(f"[Stress Fusion ERROR] {e}")

        time.sleep(FIREBASE_PUSH_INTERVAL)

# ---------------------------------------------------------
# MAIN — UNIFIED CAMERA LOOP
# ---------------------------------------------------------

def main():
    global sign_label, sign_confidence, sign_top3, sign_inference_running
    cap = cv2.VideoCapture(0)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH,  640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

    if not cap.isOpened():
        print("ERROR: Cannot open webcam.")
        return

    engagement_system = EngagementSystem()

    # MediaPipe Holistic for sign detection
    mp_holistic_sol = mp.solutions.holistic
    holistic = mp_holistic_sol.Holistic(
        static_image_mode=False,
        model_complexity=1,
        min_detection_confidence=0.5,
        min_tracking_confidence=0.5,
    )
    sign_frame_buffer = deque(maxlen=SIGN_SEQUENCE_LEN)
    sign_pred_history = deque(maxlen=SIGN_PRED_SMOOTHING)

    threading.Thread(target=stress_fusion_loop, daemon=True).start()

    frame_count = 0
    print(f"Bio-Glove Monitor running for {STUDENT_ID}. Press 'q' to quit.")

    while True:
        ret, frame = cap.read()
        if not ret:
            print("Camera read failed.")
            break

        frame = cv2.flip(frame, 1)

        # DeepFace — every 30 frames in a background thread
        if frame_count % 30 == 0:
            threading.Thread(target=analyze_face, args=(frame.copy(),), daemon=True).start()

        # MediaPipe engagement — every frame (sync, fast)
        engagement_system.process(frame)

        # --- MediaPipe Holistic (sign landmarks) ---
        if SIGN_MODEL_AVAILABLE:
            rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            holistic_results = holistic.process(rgb_frame)
            draw_landmarks_styled(frame, holistic_results)

            lm_vec = extract_landmarks(holistic_results)
            sign_frame_buffer.append(lm_vec)

            if len(sign_frame_buffer) == SIGN_SEQUENCE_LEN and not sign_inference_running:
                sign_inference_running = True
                threading.Thread(
                    target=run_sign_inference,
                    args=(list(sign_frame_buffer),),
                    daemon=True,
                ).start()

        # --- UI OVERLAY ---
        with emotion_lock:
            em_label = emotion_label
            em_score = emotion_score

        # Background panel
        cv2.rectangle(frame, (5, 5), (380, 200), (30, 30, 30), -1)

        def put(text, pos, scale=0.65, color=(255, 255, 255), thick=2):
            cv2.putText(frame, text, pos, cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), thick + 2)
            cv2.putText(frame, text, pos, cv2.FONT_HERSHEY_SIMPLEX, scale, color, thick)

        if em_score >= 0.8:
            em_color = (0, 0, 255)
        elif em_score >= 0.4:
            em_color = (0, 200, 255)
        else:
            em_color = (0, 255, 0)

        put(f"Emotion : {em_label}",           (10, 35),  color=em_color)
        put(f"Em Score: {em_score:.2f}",        (10, 65),  color=em_color)

        engagement_system.draw(frame)

        # Show detected sign in top panel
        if SIGN_MODEL_AVAILABLE:
            with sign_lock:
                s_label, s_conf = sign_label, sign_confidence
            if s_label:
                sign_color = (0, 230, 118) if s_conf >= SIGN_CONF_THRESHOLD else (0, 165, 255)
                put(f"Sign: {s_label.upper()} ({s_conf:.2f})", (10, 195), color=sign_color)
            else:
                put("Sign: Waiting...", (10, 195), color=(150, 150, 150))

        # Sign detection bottom panel
        if SIGN_MODEL_AVAILABLE:
            buffer_fill = len(sign_frame_buffer) / SIGN_SEQUENCE_LEN
            with sign_lock:
                s_label, s_conf, s_top3 = sign_label, sign_confidence, sign_top3
            draw_prediction_panel(frame, s_label, s_conf, buffer_fill, s_top3)

        frame_count += 1
        cv2.imshow("Bio-Glove Monitor", frame)

        key = cv2.waitKey(1) & 0xFF
        if key == ord("q"):
            break
        elif key == ord("r") and SIGN_MODEL_AVAILABLE:
            sign_frame_buffer.clear()
            sign_pred_history.clear()
            with sign_lock:
                sign_label      = None
                sign_confidence = 0.0
                sign_top3       = None
            print("[INFO] Sign buffer reset.")

    holistic.close()
    cap.release()
    cv2.destroyAllWindows()
    print("Monitor stopped.")


if __name__ == "__main__":
    main()
