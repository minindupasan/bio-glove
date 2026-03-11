"""
Real-Time Sign Language Recognition via Webcam.


Usage:
    python realtime.py

Controls:
    q  - Quit
    r  - Reset prediction buffer
"""
import os
import cv2
import numpy as np
import mediapipe as mp
from collections import deque
from config import (
    MODEL_DIR, SEQUENCE_LENGTH, TOTAL_FEATURES,
    NUM_POSE_LANDMARKS, NUM_HAND_LANDMARKS, FEATURES_PER_LANDMARK,
    MP_MIN_DETECTION_CONFIDENCE, MP_MIN_TRACKING_CONFIDENCE,
    CONFIDENCE_THRESHOLD, PREDICTION_SMOOTHING,
    WEBCAM_WIDTH, WEBCAM_HEIGHT,
)


# ──────────────────────────────────────────────
# Colors (BGR)
# ──────────────────────────────────────────────
COLOR_TEXT = (255, 255, 255)
COLOR_ACCENT = (0, 230, 118)
COLOR_LOW_CONF = (0, 165, 255)
COLOR_POSE = (80, 110, 200)
COLOR_HAND_L = (255, 120, 80)
COLOR_HAND_R = (80, 200, 255)
COLOR_PANEL = (40, 40, 40)


def extract_landmarks(results):
    """Extract flat feature vector from MediaPipe Holistic results."""
    features = []

    if results.pose_landmarks:
        for lm in results.pose_landmarks.landmark:
            features.extend([lm.x, lm.y, lm.z])
    else:
        features.extend([0.0] * NUM_POSE_LANDMARKS * FEATURES_PER_LANDMARK)

    if results.left_hand_landmarks:
        for lm in results.left_hand_landmarks.landmark:
            features.extend([lm.x, lm.y, lm.z])
    else:
        features.extend([0.0] * NUM_HAND_LANDMARKS * FEATURES_PER_LANDMARK)

    if results.right_hand_landmarks:
        for lm in results.right_hand_landmarks.landmark:
            features.extend([lm.x, lm.y, lm.z])
    else:
        features.extend([0.0] * NUM_HAND_LANDMARKS * FEATURES_PER_LANDMARK)

    return np.array(features, dtype=np.float32)


def draw_landmarks_styled(frame, results):
    """Draw pose and hand landmarks with custom styling."""
    mp_drawing = mp.solutions.drawing_utils
    mp_holistic = mp.solutions.holistic

    pose_style = mp_drawing.DrawingSpec(color=COLOR_POSE, thickness=2, circle_radius=2)
    hand_l_style = mp_drawing.DrawingSpec(color=COLOR_HAND_L, thickness=2, circle_radius=3)
    hand_r_style = mp_drawing.DrawingSpec(color=COLOR_HAND_R, thickness=2, circle_radius=3)
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
    h, w = frame.shape[:2]
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
        color = COLOR_ACCENT if confidence >= CONFIDENCE_THRESHOLD else COLOR_LOW_CONF
        label_text = prediction.upper()

        cv2.putText(frame, label_text,
                    (20, panel_y + 60),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.2, color, 3)

        conf_text = f"Similarity: {confidence:.2f}"
        cv2.putText(frame, conf_text,
                    (20, panel_y + 85),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)

        # Show Top-3 predictions
        if top3:
            y_offset = panel_y + 50
            x_offset = w - 300
            cv2.putText(frame, "Top-3:", (x_offset, y_offset),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, COLOR_TEXT, 1)
            for i, (t3_label, t3_conf) in enumerate(top3):
                y_pos = y_offset + 20 + i * 22
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


def main():
    print("=" * 60)
    print("  Real-Time Sign Language Recognition")
    print("  (Prototypical Network — Cosine Similarity)")
    print("=" * 60)

    # ── Load Model ─────────────────────────────────────
    encoder_path = os.path.join(MODEL_DIR, "encoder.keras")
    proto_path = os.path.join(MODEL_DIR, "prototypes.npy")
    label_path = os.path.join(MODEL_DIR, "label_encoder.npy")

    # Check for prototypical network files first, fallback to old model
    use_protonet = os.path.exists(encoder_path) and os.path.exists(proto_path)

    if use_protonet:
        import tensorflow as tf

        # Register custom layers for model loading
        @tf.keras.utils.register_keras_serializable()
        class ReduceSumLayer(tf.keras.layers.Layer):
            def call(self, x):
                return tf.reduce_sum(x, axis=1)

        @tf.keras.utils.register_keras_serializable()
        class L2NormalizeLayer(tf.keras.layers.Layer):
            def call(self, x):
                return tf.math.l2_normalize(x, axis=1)

        print("\nLoading Prototypical Network encoder + prototypes...")
        encoder = tf.keras.models.load_model(
            encoder_path, safe_mode=False,
            custom_objects={'ReduceSumLayer': ReduceSumLayer, 'L2NormalizeLayer': L2NormalizeLayer}
        )
        prototypes = np.load(proto_path)
        labels = np.load(label_path, allow_pickle=True)
        print(f"Loaded. {len(labels)} sign classes, embedding dim={prototypes.shape[1]}")
    else:
        # Fallback to old classification model
        model_path = os.path.join(MODEL_DIR, "sign_language_model.keras")
        if not os.path.exists(model_path):
            print(f"[ERROR] No model found in {MODEL_DIR}")
            print("        Run `python train.py` first.")
            return
        import tensorflow as tf
        print("\nLoading classification model...")
        encoder = tf.keras.models.load_model(model_path)
        labels = np.load(label_path, allow_pickle=True)
        prototypes = None
        print(f"Loaded. {len(labels)} sign classes.")

    # ── Initialize MediaPipe ───────────────────────────
    mp_holistic = mp.solutions.holistic
    holistic = mp_holistic.Holistic(
        static_image_mode=False,
        model_complexity=1,
        min_detection_confidence=MP_MIN_DETECTION_CONFIDENCE,
        min_tracking_confidence=MP_MIN_TRACKING_CONFIDENCE,
    )

    # ── Initialize Webcam ──────────────────────────────
    cap = cv2.VideoCapture(0)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, WEBCAM_WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, WEBCAM_HEIGHT)

    if not cap.isOpened():
        print("[ERROR] Cannot open webcam.")
        return

    print("\nWebcam opened. Starting recognition...")
    print("Press 'q' to quit, 'r' to reset buffer.\n")

    # ── Prediction State ───────────────────────────────
    frame_buffer = deque(maxlen=SEQUENCE_LENGTH)
    prediction_history = deque(maxlen=PREDICTION_SMOOTHING)
    current_prediction = None
    current_confidence = 0.0
    current_top3 = None

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        frame = cv2.flip(frame, 1)

        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        rgb.flags.writeable = False
        results = holistic.process(rgb)
        rgb.flags.writeable = True

        draw_landmarks_styled(frame, results)

        landmarks = extract_landmarks(results)
        frame_buffer.append(landmarks)

        buffer_fill = len(frame_buffer) / SEQUENCE_LENGTH

        if len(frame_buffer) == SEQUENCE_LENGTH:
            input_data = np.array(list(frame_buffer), dtype=np.float32)
            input_data = np.expand_dims(input_data, axis=0)

            if use_protonet and prototypes is not None:
                # Prototypical Network: embed → cosine similarity
                embedding = encoder.predict(input_data, verbose=0)[0]
                embedding = embedding / (np.linalg.norm(embedding) + 1e-8)

                similarities = embedding @ prototypes.T
                top_indices = np.argsort(similarities)[::-1]

                pred_idx = top_indices[0]
                pred_conf = similarities[pred_idx]

                top3 = [(labels[top_indices[i]], similarities[top_indices[i]])
                        for i in range(min(3, len(top_indices)))]
            else:
                # Fallback: classification model
                predictions = encoder.predict(input_data, verbose=0)[0]
                pred_idx = np.argmax(predictions)
                pred_conf = predictions[pred_idx]
                top_indices = np.argsort(predictions)[::-1]
                top3 = [(labels[top_indices[i]], predictions[top_indices[i]])
                        for i in range(min(3, len(top_indices)))]

            prediction_history.append((pred_idx, pred_conf))

            if len(prediction_history) >= 2:
                recent_indices = [p[0] for p in prediction_history]
                most_common_idx = max(set(recent_indices), key=recent_indices.count)
                avg_conf = np.mean([p[1] for p in prediction_history
                                    if p[0] == most_common_idx])

                current_prediction = labels[most_common_idx]
                current_confidence = avg_conf
                current_top3 = top3

        draw_prediction_panel(frame, current_prediction, current_confidence,
                              buffer_fill, current_top3)

        cv2.putText(frame, "Sign Language Recognition",
                    (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, COLOR_TEXT, 2)

        mode_text = "ProtoNet" if use_protonet else "Classifier"
        cv2.putText(frame, f"Mode: {mode_text}",
                    (10, 55), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (150, 150, 150), 1)

        has_hands = results.left_hand_landmarks or results.right_hand_landmarks
        status_color = COLOR_ACCENT if has_hands else (0, 0, 200)
        status_text = "Hands Detected" if has_hands else "No Hands"
        cv2.circle(frame, (15, 75), 5, status_color, -1)
        cv2.putText(frame, status_text, (25, 80),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, status_color, 1)

        cv2.imshow("Sign Language Recognition", frame)

        key = cv2.waitKey(1) & 0xFF
        if key == ord('q'):
            break
        elif key == ord('r'):
            frame_buffer.clear()
            prediction_history.clear()
            current_prediction = None
            current_confidence = 0.0
            current_top3 = None
            print("[INFO] Buffer reset.")

    cap.release()
    cv2.destroyAllWindows()
    holistic.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
