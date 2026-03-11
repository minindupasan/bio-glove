import cv2
import mediapipe as mp
import numpy as np
import time
from collections import deque
import firebase_admin
from firebase_admin import credentials, db

# ---------------------------------------------------------
# FIREBASE & STUDENT CONFIGURATION
# ---------------------------------------------------------
FIREBASE_CRED_PATH = "serviceAccountKey.json"
FIREBASE_DB_URL    = "https://smart-classroom-981e2-default-rtdb.asia-southeast1.firebasedatabase.app"
STUDENT_ID         = "s01"
FIREBASE_PUSH_INTERVAL = 2  # seconds between Firebase writes

try:
    firebase_admin.get_app()
except ValueError:
    cred = credentials.Certificate(FIREBASE_CRED_PATH)
    firebase_admin.initialize_app(cred, {"databaseURL": FIREBASE_DB_URL})

eng_ref = db.reference(f"engagement/{STUDENT_ID}")

# ==========================================
# PART 1: FEATURES (Calculations)
# ==========================================

def calculate_ear(landmarks, refer_idxs, frame_w, frame_h):
    """
    Calculates Eye Aspect Ratio (EAR) to detect blinking or drowsiness.
    """
    try:
        coords_points = []
        for i in refer_idxs:
            lm = landmarks[i]
            coord = np.array([lm.x * frame_w, lm.y * frame_h])
            coords_points.append(coord)
        
        # Calculate distances
        v1 = np.linalg.norm(coords_points[1] - coords_points[5]) # P2-P6
        v2 = np.linalg.norm(coords_points[2] - coords_points[4]) # P3-P5
        h = np.linalg.norm(coords_points[0] - coords_points[3])  # P1-P4
        
        ear = (v1 + v2) / (2.0 * h)
        return ear
    except Exception as e:
        return 0.0

def get_head_pose(landmarks, frame_w, frame_h):
    """
    Estimates Head Pose (Pitch, Yaw, Roll) using SolvePnP.
    """
    face_3d = []
    face_2d = []
    
    # Key landmarks: Nose tip, Chin, Left Eye Left Corner, Right Eye Right Corner, Mouth Left, Mouth Right
    key_landmarks = [1, 199, 33, 263, 61, 291]
    
    for idx in key_landmarks:
        lm = landmarks[idx]
        x, y = int(lm.x * frame_w), int(lm.y * frame_h)
        face_2d.append([x, y])
        face_3d.append([x, y, lm.z])
        
    face_2d = np.array(face_2d, dtype=np.float64)
    face_3d = np.array(face_3d, dtype=np.float64)

    # Camera Matrix
    focal_length = 1 * frame_w
    cam_matrix = np.array([[focal_length, 0, frame_h / 2],
                           [0, focal_length, frame_w / 2],
                           [0, 0, 1]])
    
    dist_matrix = np.zeros((4, 1), dtype=np.float64)

    # Solve PnP
    success, rot_vec, trans_vec = cv2.solvePnP(face_3d, face_2d, cam_matrix, dist_matrix)
    
    if not success:
        return 0, 0, 0

    # Convert to rotation matrix
    rmat, jac = cv2.Rodrigues(rot_vec)

    # Get angles
    angles, mtxR, mtxQ, Qx, Qy, Qz = cv2.RQDecomp3x3(rmat)
    
    x_angle = angles[0] * 360  # Pitch (Up/Down)
    y_angle = angles[1] * 360  # Yaw (Left/Right)
    z_angle = angles[2] * 360  # Roll (Tilt)
    
    return x_angle, y_angle, z_angle

def get_gaze_ratio(landmarks, frame_w, frame_h):
    """
    Calculates the horizontal gaze ratio.
    """
    left_iris_center = np.array([landmarks[468].x * frame_w, landmarks[468].y * frame_h])
    left_eye_inner = np.array([landmarks[133].x * frame_w, landmarks[133].y * frame_h])
    left_eye_outer = np.array([landmarks[33].x * frame_w, landmarks[33].y * frame_h])
    
    dist_center_to_inner = np.linalg.norm(left_iris_center - left_eye_inner)
    dist_center_to_outer = np.linalg.norm(left_iris_center - left_eye_outer)
    
    total_dist = dist_center_to_inner + dist_center_to_outer
    
    if total_dist == 0:
        return 0.5
        
    ratio = dist_center_to_inner / total_dist
    return ratio


# ==========================================
# PART 2: TRACKER LOGIC
# ==========================================

# --- CONFIGURATION ---
EAR_THRESHOLD = 0.21        
YAW_THRESHOLD = 20          # Sensitivity for Head Left/Right
PITCH_THRESHOLD = 15        # Sensitivity for Head Up/Down
HISTORY_LENGTH = 20         

class EngagementSystem:
    def __init__(self):
        self.mp_face_mesh = mp.solutions.face_mesh
        self.face_mesh = self.mp_face_mesh.FaceMesh(
            max_num_faces=1,
            refine_landmarks=True,
            min_detection_confidence=0.5,
            min_tracking_confidence=0.5
        )
        self.engagement_history = deque(maxlen=HISTORY_LENGTH)
        self.last_push_time = 0

    def draw_text_with_outline(self, img, text, pos, font_scale, color, thickness=2):
        x, y = pos
        cv2.putText(img, text, (x, y), cv2.FONT_HERSHEY_SIMPLEX, font_scale, (0, 0, 0), thickness + 3)
        cv2.putText(img, text, (x, y), cv2.FONT_HERSHEY_SIMPLEX, font_scale, color, thickness)

    def run(self):
        cap = cv2.VideoCapture(0)
        print("Starting... Press 'q' to exit.")
        
        while cap.isOpened():
            ret, frame = cap.read()
            if not ret:
                break
                
            frame = cv2.flip(frame, 1) # Mirror the image
            h, w, c = frame.shape
            rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            results = self.face_mesh.process(rgb_frame)
            
            status_text = "Searching..."
            status_color = (200, 200, 200)
            is_engaged_frame = False 
            
            if results.multi_face_landmarks:
                for face_landmarks in results.multi_face_landmarks:
                    lm = face_landmarks.landmark
                    
                    # 1. GET FEATURES
                    pitch, yaw, roll = get_head_pose(lm, w, h)
                    gaze_ratio = get_gaze_ratio(lm, w, h)
                    
                    left_ear = calculate_ear(lm, [362, 385, 387, 263, 373, 380], w, h)
                    right_ear = calculate_ear(lm, [33, 160, 158, 133, 153, 144], w, h)
                    avg_ear = (left_ear + right_ear) / 2.0
                    
                    # 2. LOGIC FUSION (UPDATED FOR DROWSINESS PRIORITY)
                    is_engaged_frame = True 
                    
                    # --- PRIORITY 1: DROWSINESS (Eyes Closed?) ---
                    # Check eyes first. If closed, ignore head direction.
                    if avg_ear < EAR_THRESHOLD:
                        if pitch < -PITCH_THRESHOLD:
                             status_text = "SLEEPING / NODDING" # Eyes closed + Head down
                        else:
                             status_text = "DROWSY / BLINK"     # Eyes closed only
                        status_color = (0, 0, 255) # Red Alert
                        is_engaged_frame = False

                    # --- PRIORITY 2: HEAD DIRECTION (Only checked if eyes are open) ---
                    else:
                        # Logic A: Head Yaw (Left/Right)
                        if yaw > YAW_THRESHOLD:
                            status_text = "Looking RIGHT (Head)" 
                            status_color = (0, 0, 255)
                            is_engaged_frame = False
                        elif yaw < -YAW_THRESHOLD:
                            status_text = "Looking LEFT (Head)" 
                            status_color = (0, 0, 255)
                            is_engaged_frame = False
                        
                        # Logic B: Head Pitch (Up/Down)
                        elif abs(yaw) < 15: 
                            if pitch > PITCH_THRESHOLD:
                                status_text = "Looking UP"
                                status_color = (0, 0, 255)
                                is_engaged_frame = False
                            elif pitch < -PITCH_THRESHOLD:
                                status_text = "Looking DOWN / Reading" # Reading mode
                                status_color = (0, 140, 255) # Orange (Warning but not Disengaged)
                                is_engaged_frame = True      # Count as ENGAGED because they might be reading
                        
                        # Logic D: Gaze (Eyes only)
                        # Only check eye gaze if head is stable
                        if is_engaged_frame:
                            if gaze_ratio > 0.60: 
                                status_text = "Looking Away (Eyes)"
                                status_color = (0, 140, 255)
                                is_engaged_frame = False
                            elif gaze_ratio < 0.40:
                                status_text = "Looking Away (Eyes)"
                                status_color = (0, 140, 255)
                                is_engaged_frame = False
                    
                    # Final Status Set
                    if is_engaged_frame and "Reading" not in status_text:
                        status_text = "ENGAGED"
                        status_color = (0, 255, 0)

                    # 3. SCORING
                    self.engagement_history.append(1 if is_engaged_frame else 0)
                    score = 0
                    if len(self.engagement_history) > 0:
                        score = sum(self.engagement_history) / len(self.engagement_history) * 100
                    
                    # 4. PUSH TO FIREBASE (throttled)
                    now = time.time()
                    if now - self.last_push_time >= FIREBASE_PUSH_INTERVAL:
                        try:
                            eng_ref.update({
                                "engagement_score": round(score, 1),
                                "engagement_status": status_text,
                            })
                            self.last_push_time = now
                        except Exception as e:
                            print(f"Firebase error: {e}")

                    # 5. DRAW UI
                    self.draw_text_with_outline(frame, f"STATUS: {status_text}", (20, 50), 0.8, status_color, 2)
                    
                    score_color = (0, 255, 0) if score > 50 else (0, 0, 255)
                    self.draw_text_with_outline(frame, f"SCORE: {int(score)}%", (20, 100), 1.2, score_color, 3)
                    
                    # DEBUG INFO
                    debug_info = f"EAR:{avg_ear:.2f} P:{int(pitch)} Y:{int(yaw)}"
                    self.draw_text_with_outline(frame, debug_info, (20, 140), 0.6, (200, 200, 200), 1)

            cv2.imshow('Engagement Tracker (Sleep Fixed)', frame)
            
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break
                
        cap.release()
        cv2.destroyAllWindows()

if __name__ == "__main__":
    system = EngagementSystem()
    system.run()