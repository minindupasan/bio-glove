import os
os.environ["PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION"] = "python"
import cv2
from deepface import DeepFace
import threading
import time
import firebase_admin
from firebase_admin import credentials, db

# ---------------------------------------------------------
# FIREBASE & STUDENT CONFIGURATION
# ---------------------------------------------------------
FIREBASE_CRED_PATH = "serviceAccountKey.json"
FIREBASE_DB_URL    = "https://smart-classroom-981e2-default-rtdb.asia-southeast1.firebasedatabase.app"
STUDENT_ID         = "s01"  # Ensure this matches the live Predictor Target

print("Connecting to Firebase Desktop Application...")
try:
    firebase_admin.get_app()
except ValueError:
    cred = credentials.Certificate(FIREBASE_CRED_PATH)
    firebase_admin.initialize_app(cred, {"databaseURL": FIREBASE_DB_URL})

root_ref = db.reference("smartglove")
emotion_ref = db.reference(f"emotion/{STUDENT_ID}")

# 1. Camera Initialization
cap = cv2.VideoCapture(0) # 0 for Webcam
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

current_emotion = "Analyze.."
last_pushed_emotion = None
stress_status = "Checking..."
color = (255, 255, 255)
emotion_score = 0.0

def push_emotion_to_firebase(emotion_label, em_score):
    """ Uploads the latest emotion metadata asynchronously to be fused by the Predictor. """
    try:
        root_ref.child(f"{STUDENT_ID}/camera_emotion").set({
            "emotion": emotion_label,
            "emotion_score": em_score,
            "timestamp": int(time.time() * 1000)
        })
        emotion_ref.set({
            "stress_score": em_score,
            "emotion_label": emotion_label,
            "timestamp": int(time.time() * 1000)
        })
    except Exception as e:
        import traceback
        print(f"Firebase Update Error: {e}")
        traceback.print_exc()

def analyze_face(frame):
    global current_emotion, last_pushed_emotion, stress_status, color, emotion_score
    
    try:
        # DeepFace Emotion Extraction
        result = DeepFace.analyze(frame, actions=['emotion'], enforce_detection=False)
        
        if isinstance(result, list):
            result = result[0]
            
        dominant_emotion = result['dominant_emotion']
        current_emotion = dominant_emotion

        # --- STRESS ALGORITHM MAPPING ---
        if dominant_emotion in ['angry', 'fear', 'sad', 'disgust']:
            stress_status = "HIGH STRESS"
            color = (0, 0, 255) # Red
            emotion_score = 1.0
        elif dominant_emotion in ['neutral', 'happy']:
            stress_status = "RELAXED"
            color = (0, 255, 0) # Green
            emotion_score = 0.0
        else:
            stress_status = "MODERATE" # Surprise etc.
            color = (0, 255, 255) # Yellow
            emotion_score = 0.5
            
        if dominant_emotion != last_pushed_emotion:
            push_emotion_to_firebase(current_emotion, emotion_score)
            last_pushed_emotion = dominant_emotion
            print(f"Emotion changed → {dominant_emotion} (score: {emotion_score})")

    except Exception as e:
        print("Analysis Error:", e)

# Main Loop
frame_count = 0

print(f"System Started. Streaming to Firebase for {STUDENT_ID}. Press 'q' to exit.")

while True:
    ret, frame = cap.read()
    if not ret:
        break

    frame = cv2.flip(frame, 1)

    # Process CV 1x per second (30 fps) to avoid blocking
    if frame_count % 30 == 0:
        threading.Thread(target=analyze_face, args=(frame.copy(),)).start()

    frame_count += 1

    # --- UI Display ---
    cv2.rectangle(frame, (10, 10), (300, 120), (50, 50, 50), -1) 
    
    cv2.putText(frame, f"Emotion: {current_emotion}", (30, 50), 
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
    
    cv2.putText(frame, f"Status: {stress_status}", (30, 90), 
                cv2.FONT_HERSHEY_SIMPLEX, 1, color, 3)

    cv2.imshow('Computer Vision Stress Detection', frame)

    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

cap.release()
cv2.destroyAllWindows()