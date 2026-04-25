"""
SALE Component 3 — Emotion / Visual Stress Module
===================================================
Computes Svis (visual stress score) from webcam frames using DeepFace.

Svis formula:
    Svis = P(angry) + P(fear) + P(sad) + 0.4 × P(disgust)

Deaf-classroom adaptation:
    Sign language facial grammar (brow furrow for Wh-questions,
    brow raise for yes/no) is detected and Svis is suppressed
    so grammar markers are not misread as stress expressions.

Usage:
    from emotion import EmotionDetector

    detector = EmotionDetector()
    detector.start_preload()        # call once at startup (non-blocking)

    # In your frame loop:
    detector.submit_frame(bgr_frame)        # fire-and-forget background thread
    Svis, emotions = detector.get_result()  # latest result (non-blocking)
"""

import threading
import numpy as np
import cv2

from config import DEEPFACE_INTERVAL

# ── Try to import DeepFace ────────────────────────────────────────────────────
try:
    from deepface import DeepFace
    DEEPFACE_AVAILABLE = True
except ImportError:
    DEEPFACE_AVAILABLE = False


# ── MediaPipe brow landmarks for SL grammar detection ────────────────────────
_L_BROW_IN  = 107;  _R_BROW_IN  = 336
_L_BROW_OUT = 70;   _R_BROW_OUT = 300
_L_EYE_TOP  = 159;  _R_EYE_TOP  = 386
_NOSE = 1;          _CHIN = 152

# HSV skin detection range for hand/signing detection
_SKIN_LO = np.array([0,  20,  70],  dtype=np.uint8)
_SKIN_HI = np.array([25, 255, 255], dtype=np.uint8)
_KERNEL  = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))


# ══════════════════════════════════════════════════════════════════════════════
# SIGNING DETECTOR
# Detects active hand-signing via skin-blob HSV detection and brow geometry.
# Suppresses Svis when grammar markers are present.
# ══════════════════════════════════════════════════════════════════════════════

class SigningDetector:
    """
    Detects active signing to prevent SL facial grammar from raising Svis.

    Two signals:
      1. Skin-blob detection in upper frame (hand elevation above waist)
      2. Brow geometry from Face Mesh (furrow = Wh-question, raise = Y/N)

    Output:
      svis_multiplier() → float in [0.0, 1.0] to scale raw Svis
    """

    CONFIRM   = 4     # consecutive frames to confirm signing
    RELEASE   = 10    # frames of no signing before clearing flag
    MIN_BLOB  = 1200  # minimum skin blob area in px²

    def __init__(self):
        self._consec_on  = 0
        self._consec_off = 0
        self.is_signing  = False
        self.confidence  = 0.0
        self.brow_raised   = False
        self.brow_furrowed = False

    def update(self, frame_bgr: np.ndarray,
               face_lm, img_w: int, img_h: int) -> None:
        """
        Update signing state from one frame.

        Args:
            frame_bgr: Full BGR frame from webcam
            face_lm:   MediaPipe face mesh landmark list (or None if no face)
            img_w:     Frame width in pixels
            img_h:     Frame height in pixels
        """
        # ── Skin blob detection (upper 65% of frame, face region masked) ──────
        zone = frame_bgr[:int(img_h * 0.65), :]
        hsv  = cv2.cvtColor(zone, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, _SKIN_LO, _SKIN_HI)

        # Blank out face region to avoid false positives from the face itself
        cx = img_w // 2; fw = int(img_w * 0.18); fy = int(img_h * 0.25)
        mask[:fy, cx - fw:cx + fw] = 0

        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN,  _KERNEL)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, _KERNEL)

        cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL,
                                   cv2.CHAIN_APPROX_SIMPLE)
        blobs      = [c for c in cnts if cv2.contourArea(c) > self.MIN_BLOB]
        blob_area  = sum(cv2.contourArea(c) for c in blobs)
        signing_now = len(blobs) > 0
        conf = float(np.clip(
            blob_area / (zone.shape[0] * zone.shape[1] + 1e-6) * 20, 0, 1))

        # State machine
        if signing_now:
            self._consec_on  += 1
            self._consec_off  = 0
        else:
            self._consec_off += 1
            self._consec_on   = 0

        if self._consec_on  >= self.CONFIRM:
            self.is_signing = True;  self.confidence = conf
        if self._consec_off >= self.RELEASE:
            self.is_signing = False; self.confidence = 0.0

        # ── Brow geometry (SL grammar markers from Face Mesh) ─────────────────
        self.brow_raised = self.brow_furrowed = False
        if face_lm:
            face_h = abs(face_lm[_CHIN].y - face_lm[_NOSE].y) + 1e-6
            brow_gap = ((face_lm[_L_EYE_TOP].y - face_lm[_L_BROW_IN].y) +
                        (face_lm[_R_EYE_TOP].y - face_lm[_R_BROW_IN].y)) / 2
            # Raised brows (Y/N questions)
            if brow_gap / face_h > 0.192:
                self.brow_raised = True
            # Furrowed brows (Wh-questions)
            outer = abs(face_lm[_R_BROW_OUT].x - face_lm[_L_BROW_OUT].x) + 1e-6
            inner = abs(face_lm[_R_BROW_IN].x  - face_lm[_L_BROW_IN].x)
            if inner / outer < 0.282:
                self.brow_furrowed = True

    def svis_multiplier(self) -> float:
        """
        Returns a suppression factor for Svis:
          1.0 = no suppression (not signing)
          0.0 = full suppression (confident active signing)
        """
        if self.is_signing:
            return 1.0 - 0.80 * self.confidence
        if self.brow_raised or self.brow_furrowed:
            return 0.60
        return 1.0

    def fusion_shift(self) -> tuple[float, float]:
        """
        During active signing, shift fusion weight toward Sphys
        since Svis is less reliable.

        Returns: (w_phys, w_vis) adjusted weights
        """
        from config import W_PHYS, W_VIS
        if not self.is_signing:
            return W_PHYS, W_VIS
        c = self.confidence
        return min(1.0, W_PHYS + c * 0.20), max(0.0, W_VIS - c * 0.20)


# ══════════════════════════════════════════════════════════════════════════════
# EMOTION DETECTOR
# Thread-safe DeepFace wrapper with preload and non-blocking frame submission
# ══════════════════════════════════════════════════════════════════════════════

def _raw_svis(emotions: dict) -> float:
    """
    Compute Svis from DeepFace emotion probabilities.
    Svis = P(angry) + P(fear) + P(sad) + 0.4 × P(disgust)
    """
    if not emotions:
        return 0.0
    total = sum(emotions.values()) + 1e-9
    p = {k: v / total for k, v in emotions.items()}
    return float(np.clip(
        p.get('angry', 0) + p.get('fear', 0) +
        p.get('sad',   0) + 0.4 * p.get('disgust', 0),
        0.0, 1.0
    ))


class EmotionDetector:
    """
    Non-blocking DeepFace emotion detector.

    Runs analysis in a background thread every DEEPFACE_INTERVAL frames.
    Caller gets the most recent result without blocking the main loop.
    """

    def __init__(self):
        self._lock     = threading.Lock()
        self._busy     = threading.Event()
        self._svis     = 0.0
        self._emotions = {}
        self._frame_n  = 0
        self._available = DEEPFACE_AVAILABLE

        if not self._available:
            print("[INFO] DeepFace not available — Svis will be 0.0")

    def start_preload(self) -> None:
        """
        Preload DeepFace model at startup using a dummy frame.
        Prevents 15–30s freeze on first real frame.
        Call once before the main loop starts.
        """
        if not self._available:
            return
        print("  [LOADING] DeepFace model — may take 15–30s on first run...",
              flush=True)

        def _load():
            try:
                dummy = np.zeros((48, 48, 3), dtype=np.uint8)
                DeepFace.analyze(dummy, actions=['emotion'],
                                 enforce_detection=False, silent=True)
                print("  [LOADING] DeepFace ready.", flush=True)
            except Exception:
                pass

        threading.Thread(target=_load, daemon=True).start()

    def submit_frame(self, bgr_frame: np.ndarray) -> None:
        """
        Submit a frame for emotion analysis (non-blocking).
        Analysis runs in background — skipped if previous analysis is still running.

        Args:
            bgr_frame: BGR frame from OpenCV
        """
        if not self._available:
            return

        self._frame_n += 1
        if self._frame_n % DEEPFACE_INTERVAL != 0:
            return
        if self._busy.is_set():
            return

        self._busy.set()
        frame_copy = bgr_frame.copy()
        threading.Thread(target=self._analyse,
                         args=(frame_copy,), daemon=True).start()

    def _analyse(self, bgr: np.ndarray) -> None:
        """Background DeepFace analysis thread."""
        try:
            result = DeepFace.analyze(bgr, actions=['emotion'],
                                      enforce_detection=False,
                                      detector_backend='opencv',
                                      silent=True)
            em = result[0]['emotion'] if isinstance(result, list) \
                else result['emotion']
            with self._lock:
                self._emotions = em
                self._svis     = _raw_svis(em)
        except Exception:
            pass
        finally:
            self._busy.clear()

    def get_result(self) -> tuple[float, dict]:
        """
        Get the latest emotion analysis result (non-blocking, thread-safe).

        Returns:
            (Svis_raw, emotions_dict)
            Svis_raw is BEFORE signing suppression — apply svis_multiplier() separately.
        """
        with self._lock:
            return self._svis, dict(self._emotions)
