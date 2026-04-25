"""
SALE Component 3 — Engagement Module
======================================
Computes E (engagement score) from MediaPipe Face Mesh landmarks.

Deaf-classroom calibrations applied:
  - Interpreter zone gaze is NOT penalised
    (deaf students naturally alternate between teacher and interpreter)
  - Downward pitch (reading/board-looking) is NOT penalised
  - Sign-following gaze is NOT penalised during active signing
  - EAR threshold tuned for alert-but-focused face shape

Engagement score E ∈ [0.0, 1.0]:
  1.0 = fully engaged (eyes open, face forward or toward interpreter)
  0.0 = disengaged (closed eyes, face away, or completely turned)

Usage:
    from engagement import EngagementEstimator

    estimator = EngagementEstimator(cam_w=1280, cam_h=720)

    # In your MediaPipe frame loop:
    if mp_results.multi_face_landmarks:
        lm = mp_results.multi_face_landmarks[0].landmark
        E  = estimator.update(lm)
    else:
        E  = estimator.last_score   # hold last known value
"""

import collections
import cv2
import numpy as np

from config import SMOOTHING_WIN


# ── MediaPipe landmark indices ────────────────────────────────────────────────
_L_EAR_IDX = [362, 385, 387, 263, 373, 380]
_R_EAR_IDX = [33,  160, 158, 133, 153, 144]
_L_IRIS    = [474, 475, 476, 477]
_R_IRIS    = [469, 470, 471, 472]
_L_EYE     = [362, 382, 381, 380, 374, 373, 390, 249,
              263, 466, 388, 387, 386, 385, 384, 398]
_R_EYE     = [33,  7,   163, 144, 145, 153, 154, 155,
              133, 173, 157, 158, 159, 160, 161, 246]
_POSE_IDX  = [1,   199, 33,  263, 61,  291]
_POSE_3D   = np.array([
    [0.,    0.,    0.  ],
    [0.,   -330., -65. ],
    [-225., 170., -135.],
    [225.,  170., -135.],
    [-150.,-150., -125.],
    [150., -150., -125.]
], dtype=np.float64)

# Engagement score thresholds
_EAR_BLINK_THRESH  = 0.18   # below this → likely blinking
_EAR_DROWSY_THRESH = 0.22   # below this → reduced alertness
_GAZE_THRESH       = 0.40   # gaze offset ratio — above + head turned = looking away
_YAW_INTERPRETER   = 40.0   # degrees — within this range looking at interpreter is OK
_YAW_FAR           = 70.0   # degrees — beyond this is clearly disengaged
_PITCH_DOWN_OK     = 30.0   # degrees downward — reading/notes, acceptable
_BLINK_CONFIRM     = 3      # consecutive low-EAR frames to confirm blink


# ══════════════════════════════════════════════════════════════════════════════
# LOW-LEVEL LANDMARK HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _ear(lm, idx: list, w: int, h: int) -> float:
    """Eye Aspect Ratio — lower = more closed."""
    pts = np.array([[lm[i].x * w, lm[i].y * h] for i in idx])
    A   = np.linalg.norm(pts[1] - pts[5])
    B   = np.linalg.norm(pts[2] - pts[4])
    C   = np.linalg.norm(pts[0] - pts[3]) + 1e-6
    return float((A + B) / (2 * C))


def _gaze_offset(lm, iris_idx: list, eye_idx: list, w: int, h: int) -> float:
    """
    Gaze offset ratio — how far the iris centre is from the eye centre.
    0.0 = looking straight ahead; higher = looking sideways.
    """
    iris_pts = np.array([[lm[i].x * w, lm[i].y * h] for i in iris_idx])
    eye_pts  = np.array([[lm[i].x * w, lm[i].y * h] for i in eye_idx[:8]])
    iris_c   = iris_pts.mean(0)
    eye_c    = eye_pts.mean(0)
    eye_w    = np.linalg.norm(eye_pts.max(0) - eye_pts.min(0)) + 1e-6
    return float(np.linalg.norm(iris_c - eye_c) / eye_w)


def _head_pose(lm, w: int, h: int) -> tuple[float, float, float]:
    """
    Estimate head pose (pitch, yaw, roll) in degrees using PnP.

    Returns: (pitch, yaw, roll)
      pitch > 0 → looking down
      yaw   > 0 → turned right
    """
    p2d = np.array([[lm[i].x * w, lm[i].y * h] for i in _POSE_IDX],
                   dtype=np.float64)
    cam_matrix = np.array([[w, 0, w / 2],
                            [0, w, h / 2],
                            [0, 0, 1   ]], dtype=np.float64)
    ok, rvec, _ = cv2.solvePnP(
        _POSE_3D, p2d, cam_matrix, np.zeros((4, 1)),
        flags=cv2.SOLVEPNP_ITERATIVE
    )
    if not ok:
        return 0.0, 0.0, 0.0
    R, _ = cv2.Rodrigues(rvec)
    sy   = np.sqrt(R[0, 0] ** 2 + R[1, 0] ** 2)
    pitch = float(np.degrees(np.arctan2(-R[2, 0], sy)))
    yaw   = float(np.degrees(np.arctan2(R[1, 0],  R[0, 0])))
    roll  = float(np.degrees(np.arctan2(R[2, 1],  R[2, 2])))
    return pitch, yaw, roll


# ══════════════════════════════════════════════════════════════════════════════
# ENGAGEMENT ESTIMATOR
# ══════════════════════════════════════════════════════════════════════════════

class EngagementEstimator:
    """
    Computes a smoothed engagement score E ∈ [0.0, 1.0] from Face Mesh.

    Deaf-classroom calibrations:
      - Interpreter zone (within ±YAW_INTERPRETER degrees) is not penalised
      - Downward pitch (reading/board) is not penalised
      - Active signing: lateral gaze is not penalised

    Parameters:
        cam_w, cam_h:      Camera resolution (used for landmark scaling)
        interp_yaw:        Yaw angle of interpreter relative to student (degrees)
        smoothing_win:     Rolling average window for E
    """

    def __init__(self, cam_w: int = 1280, cam_h: int = 720,
                 interp_yaw: float = _YAW_INTERPRETER,
                 smoothing_win: int = SMOOTHING_WIN):
        self.w            = cam_w
        self.h            = cam_h
        self.interp_yaw   = interp_yaw
        self._ear_buf     = collections.deque(maxlen=10)
        self._score_buf   = collections.deque(maxlen=smoothing_win)
        self._blink_ctr   = 0
        self._blink_flag  = False
        self.last_score   = 0.5   # neutral default when no face detected
        self.last_pitch   = 0.0
        self.last_yaw     = 0.0
        self.has_iris     = False

    def update(self, lm, is_signing: bool = False) -> float:
        """
        Compute engagement for one frame.

        Args:
            lm:          MediaPipe face mesh landmark list
            is_signing:  Whether SigningDetector reports active signing

        Returns:
            E ∈ [0.0, 1.0] — smoothed engagement score
        """
        w, h = self.w, self.h

        # ── EAR (eye openness) ───────────────────────────────────────────────
        ear_l = _ear(lm, _L_EAR_IDX, w, h)
        ear_r = _ear(lm, _R_EAR_IDX, w, h)
        ear   = (ear_l + ear_r) / 2
        self._ear_buf.append(ear)

        # Blink detection
        if ear < _EAR_BLINK_THRESH:
            self._blink_ctr += 1
        else:
            self._blink_flag  = self._blink_ctr >= _BLINK_CONFIRM
            self._blink_ctr   = 0

        mean_ear = float(np.mean(self._ear_buf)) if self._ear_buf else 0.25

        # ── Gaze offset ──────────────────────────────────────────────────────
        gaze = 0.25   # default (no iris data)
        self.has_iris = len(lm) >= 478
        if self.has_iris:
            try:
                gaze = (_gaze_offset(lm, _L_IRIS, _L_EYE, w, h) +
                        _gaze_offset(lm, _R_IRIS, _R_EYE, w, h)) / 2
            except Exception:
                pass

        # ── Head pose ────────────────────────────────────────────────────────
        try:
            pitch, yaw, _ = _head_pose(lm, w, h)
        except Exception:
            pitch, yaw = 0.0, 0.0
        self.last_pitch = pitch
        self.last_yaw   = yaw

        # ── Score computation ─────────────────────────────────────────────────
        score = 1.0

        # Alertness (EAR)
        if mean_ear < _EAR_BLINK_THRESH:
            score -= 0.40
        elif mean_ear < _EAR_DROWSY_THRESH:
            score -= 0.15

        # Gaze + yaw (only penalise if both lateral gaze AND head turned)
        # — interpreter zone is OK (within ±interp_yaw)
        # — during signing, lateral gaze is OK
        yaw_abs = abs(yaw)
        if not is_signing:
            in_interpreter_zone = yaw_abs <= self.interp_yaw
            if not in_interpreter_zone and gaze > _GAZE_THRESH:
                score -= min(0.30, (gaze - 0.35) * 1.2)

        # Hard lateral turn (beyond interpreter zone, regardless of signing)
        if yaw_abs > _YAW_FAR:
            score -= min(0.30, (yaw_abs - _YAW_FAR) / 20)

        # Downward pitch = reading / board = acceptable
        # Upward pitch = distracted
        if pitch < -20:   # looking up
            score -= min(0.20, (abs(pitch) - 20) / 20)

        # Brief blink
        if self._blink_flag:
            score -= 0.05

        score = float(np.clip(score, 0.0, 1.0))
        self._score_buf.append(score)
        self.last_score = float(np.mean(self._score_buf))
        return self.last_score

    def draw_landmarks(self, frame: np.ndarray, lm) -> None:
        """Optionally draw EAR landmark dots on the frame for debug view."""
        for idx in _L_EAR_IDX + _R_EAR_IDX:
            cx = int(lm[idx].x * self.w)
            cy = int(lm[idx].y * self.h)
            cv2.circle(frame, (cx, cy), 1, (80, 180, 80), -1)
