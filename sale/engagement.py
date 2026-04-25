"""
SALE Component 3 — Engagement Module
======================================
Computes E (engagement score ∈ [0,1]) and engagement_mode from Face Mesh.

Three engagement states:
  ENGAGED   — looking at screen/camera directly. Score 0.80–1.00.
  ATTENTIVE — looking at teacher (above camera) or book (below). Score 0.55–0.80.
              These are legitimate classroom behaviours, not distractions.
  DISENGAGED — looking sideways, eyes closed, or face absent. Score 0.00–0.55.

Pitch zones (pitch > 0 = down, pitch < 0 = up):
  Camera zone    : -10° to +10°  → ENGAGED   (score 1.0)
  Teacher zone   : -10° to -55°  → ATTENTIVE (score ~0.72, Gaussian)
  Book zone      : +10° to +50°  → ATTENTIVE (score ~0.72, Gaussian)
  Extreme up/down: beyond ±55°   → DISENGAGED (Gaussian decay)

Deaf-classroom calibrations:
  - Interpreter zone (±30° yaw) is not penalised
  - Active signing: lateral gaze is not penalised
  - Score decays exponentially toward 0 when no face detected
"""

import cv2
import numpy as np

from config import SMOOTHING_WIN

# ── MediaPipe landmark indices ─────────────────────────────────────────────────
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

# ── EAR thresholds ─────────────────────────────────────────────────────────────
_EAR_OPEN   = 0.25    # fully open
_EAR_CLOSED = 0.17    # closed / blinking

# ── Yaw thresholds ─────────────────────────────────────────────────────────────
_YAW_INTERP = 30.0    # interpreter zone — no penalty within ±30°
_YAW_SIGMA  = 25.0    # Gaussian decay beyond interpreter zone

# ── Pitch zones ────────────────────────────────────────────────────────────────
# pitch > 0 → looking down, pitch < 0 → looking up
_PITCH_CAMERA_HALF  = 10.0   # ±10° = looking at camera → ENGAGED
_PITCH_TEACHER_MAX  = 55.0   # looking up past 55° → no longer "at teacher"
_PITCH_BOOK_MAX     = 50.0   # looking down past 50° → no longer "at book"
_PITCH_ATTENTIVE_SCORE = 0.72  # score assigned inside teacher/book zones
_PITCH_DECAY_SIGMA  = 20.0   # Gaussian decay beyond attentive zones

# ── Gaze thresholds ────────────────────────────────────────────────────────────
_GAZE_CENTER = 0.15
_GAZE_SIGMA  = 0.18

# ── Component weights (must sum to 1.0) ────────────────────────────────────────
_W_EAR   = 0.28
_W_YAW   = 0.38
_W_PITCH = 0.24
_W_GAZE  = 0.10

# ── Smoothing ──────────────────────────────────────────────────────────────────
_EMA_ALPHA     = 0.18   # exponential smoothing (lower = smoother)
_NO_FACE_DECAY = 0.82   # multiply per frame when no face (~0.1 after 10 frames)

# ── State thresholds ──────────────────────────────────────────────────────────
_SCORE_ENGAGED    = 0.75   # E ≥ this → ENGAGED
_SCORE_ATTENTIVE  = 0.45   # E ≥ this → ATTENTIVE
                            # E <  this → DISENGAGED


# ── Low-level helpers ──────────────────────────────────────────────────────────

def _ear(lm, idx, w, h):
    pts = np.array([[lm[i].x * w, lm[i].y * h] for i in idx])
    A   = np.linalg.norm(pts[1] - pts[5])
    B   = np.linalg.norm(pts[2] - pts[4])
    C   = np.linalg.norm(pts[0] - pts[3]) + 1e-6
    return float((A + B) / (2.0 * C))


def _gaze_offset(lm, iris_idx, eye_idx, w, h):
    iris_pts = np.array([[lm[i].x * w, lm[i].y * h] for i in iris_idx])
    eye_pts  = np.array([[lm[i].x * w, lm[i].y * h] for i in eye_idx[:8]])
    iris_c   = iris_pts.mean(0)
    eye_c    = eye_pts.mean(0)
    eye_w    = np.linalg.norm(eye_pts.max(0) - eye_pts.min(0)) + 1e-6
    return float(np.linalg.norm(iris_c - eye_c) / eye_w)


def _head_pose(lm, w, h):
    p2d = np.array([[lm[i].x * w, lm[i].y * h] for i in _POSE_IDX],
                   dtype=np.float64)
    cam = np.array([[w, 0, w / 2],
                    [0, w, h / 2],
                    [0, 0, 1   ]], dtype=np.float64)
    ok, rvec, _ = cv2.solvePnP(_POSE_3D, p2d, cam, np.zeros((4, 1)),
                                flags=cv2.SOLVEPNP_ITERATIVE)
    if not ok:
        return 0.0, 0.0, 0.0
    R, _ = cv2.Rodrigues(rvec)
    sy   = np.sqrt(R[0, 0] ** 2 + R[1, 0] ** 2)
    pitch = float(np.degrees(np.arctan2(-R[2, 0], sy)))
    yaw   = float(np.degrees(np.arctan2(R[1, 0],  R[0, 0])))
    roll  = float(np.degrees(np.arctan2(R[2, 1],  R[2, 2])))
    return pitch, yaw, roll


def _gaussian(x, mu, sigma):
    return float(np.exp(-0.5 * ((x - mu) / sigma) ** 2))


# ── Sub-score functions ────────────────────────────────────────────────────────

def _ear_score(ear):
    """1.0 = open, 0.0 = closed, linear ramp between."""
    if ear >= _EAR_OPEN:   return 1.0
    if ear <= _EAR_CLOSED: return 0.0
    return float((ear - _EAR_CLOSED) / (_EAR_OPEN - _EAR_CLOSED))


def _yaw_score(yaw, interp_yaw, is_signing):
    """
    1.0 within interpreter zone, Gaussian decay beyond it.
    Signing: always 1.0 (gaze direction irrelevant).
    """
    if is_signing:
        return 1.0
    yaw_abs = abs(yaw)
    if yaw_abs <= interp_yaw:
        return 1.0
    excess = yaw_abs - interp_yaw
    return _gaussian(excess, 0.0, _YAW_SIGMA)


def _pitch_score_and_mode(pitch):
    """
    Returns (score, pitch_mode) where pitch_mode is one of:
      'camera'   — looking at screen
      'teacher'  — looking up at teacher / board
      'book'     — looking down at notes / book
      'away'     — too far in either direction
    """
    # Camera zone: ±_PITCH_CAMERA_HALF
    if abs(pitch) <= _PITCH_CAMERA_HALF:
        return 1.0, 'camera'

    # Teacher zone: looking up (negative pitch) within limit
    if pitch < -_PITCH_CAMERA_HALF:
        up_angle = abs(pitch) - _PITCH_CAMERA_HALF
        if abs(pitch) <= _PITCH_TEACHER_MAX:
            # Smooth ramp: 1.0 at edge of camera zone, _PITCH_ATTENTIVE_SCORE at max
            t = up_angle / (_PITCH_TEACHER_MAX - _PITCH_CAMERA_HALF)
            score = 1.0 - (1.0 - _PITCH_ATTENTIVE_SCORE) * t
            return float(score), 'teacher'
        else:
            # Beyond teacher zone — Gaussian decay
            excess = abs(pitch) - _PITCH_TEACHER_MAX
            return _gaussian(excess, 0.0, _PITCH_DECAY_SIGMA) * _PITCH_ATTENTIVE_SCORE, 'away'

    # Book zone: looking down (positive pitch) within limit
    if pitch > _PITCH_CAMERA_HALF:
        down_angle = pitch - _PITCH_CAMERA_HALF
        if pitch <= _PITCH_BOOK_MAX:
            t = down_angle / (_PITCH_BOOK_MAX - _PITCH_CAMERA_HALF)
            score = 1.0 - (1.0 - _PITCH_ATTENTIVE_SCORE) * t
            return float(score), 'book'
        else:
            excess = pitch - _PITCH_BOOK_MAX
            return _gaussian(excess, 0.0, _PITCH_DECAY_SIGMA) * _PITCH_ATTENTIVE_SCORE, 'away'

    return 1.0, 'camera'


def _gaze_score(gaze):
    return _gaussian(gaze, _GAZE_CENTER, _GAZE_SIGMA)


# ══════════════════════════════════════════════════════════════════════════════
# ENGAGEMENT ESTIMATOR
# ══════════════════════════════════════════════════════════════════════════════

class EngagementEstimator:
    """
    Computes smoothed engagement score E ∈ [0,1] and engagement_mode string.

    engagement_mode values:
      "ENGAGED"    — looking at screen, eyes open
      "ATTENTIVE"  — looking at teacher above or book below (still learning)
      "DISENGAGED" — looking sideways, eyes closed, or face absent
    """

    def __init__(self, cam_w=1280, cam_h=720,
                 interp_yaw=_YAW_INTERP,
                 smoothing_win=SMOOTHING_WIN):
        self.w            = cam_w
        self.h            = cam_h
        self.interp_yaw   = interp_yaw
        self.last_score   = 0.5
        self.last_mode    = 'ATTENTIVE'   # unknown until first frame
        self.last_pitch   = 0.0
        self.last_yaw     = 0.0
        self.has_iris     = False

        # Sub-scores exposed for debug overlay
        self.sub_ear   = 1.0
        self.sub_yaw   = 1.0
        self.sub_pitch = 1.0
        self.sub_gaze  = 1.0
        self.pitch_zone = 'camera'

    def update(self, lm, is_signing=False):
        w, h = self.w, self.h

        # EAR
        ear = (_ear(lm, _L_EAR_IDX, w, h) + _ear(lm, _R_EAR_IDX, w, h)) / 2.0
        self.sub_ear = _ear_score(ear)

        # Gaze
        self.has_iris = len(lm) >= 478
        if self.has_iris:
            try:
                gaze = (_gaze_offset(lm, _L_IRIS, _L_EYE, w, h) +
                        _gaze_offset(lm, _R_IRIS, _R_EYE, w, h)) / 2.0
                self.sub_gaze = _gaze_score(gaze)
            except Exception:
                self.sub_gaze = 0.8
        else:
            self.sub_gaze = 0.8

        # Head pose
        try:
            pitch, yaw, _ = _head_pose(lm, w, h)
        except Exception:
            pitch, yaw = 0.0, 0.0
        self.last_pitch = pitch
        self.last_yaw   = yaw

        self.sub_yaw = _yaw_score(yaw, self.interp_yaw, is_signing)
        self.sub_pitch, self.pitch_zone = _pitch_score_and_mode(pitch)

        # Weighted combination
        raw = (_W_EAR   * self.sub_ear   +
               _W_YAW   * self.sub_yaw   +
               _W_PITCH * self.sub_pitch +
               _W_GAZE  * self.sub_gaze)
        raw = float(np.clip(raw, 0.0, 1.0))

        # Exponential smoothing
        self.last_score = _EMA_ALPHA * raw + (1.0 - _EMA_ALPHA) * self.last_score
        self.last_mode  = self._classify(yaw, self.pitch_zone, self.sub_ear)
        return self.last_score

    def _classify(self, yaw, pitch_zone, ear_score):
        """Determine engagement mode from pose + eye state."""
        # Closed eyes → always disengaged
        if ear_score < 0.3:
            return 'DISENGAGED'
        # Large yaw → disengaged regardless of pitch
        if abs(yaw) > (_YAW_INTERP + _YAW_SIGMA):
            return 'DISENGAGED'
        # Teacher or book zone → attentive
        if pitch_zone in ('teacher', 'book'):
            return 'ATTENTIVE'
        # Extreme pitch (away) with large yaw → disengaged
        if pitch_zone == 'away':
            return 'DISENGAGED'
        # Camera zone, yaw OK → engaged
        return 'ENGAGED'

    def decay(self):
        """Exponential decay when no face detected."""
        self.last_score *= _NO_FACE_DECAY
        self.last_score  = float(max(0.0, self.last_score))
        self.last_mode   = 'DISENGAGED'
        return self.last_score

    def engagement_status(self):
        """
        Returns (score_0_to_100, mode_string) ready for Firebase.
        mode_string: 'ENGAGED' | 'ATTENTIVE' | 'DISENGAGED'
        """
        score_pct = int(round(self.last_score * 100))
        return score_pct, self.last_mode

    def draw_landmarks(self, frame, lm):
        for idx in _L_EAR_IDX + _R_EAR_IDX:
            cx = int(lm[idx].x * self.w)
            cy = int(lm[idx].y * self.h)
            cv2.circle(frame, (cx, cy), 1, (80, 180, 80), -1)
