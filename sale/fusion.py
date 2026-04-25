"""
SALE Component 3 — Fusion Module
==================================
Fuses physiological stress (Sphys) and visual stress (Svis) into a
single multimodal stress score St, and classifies the alert level.

Fusion formula:
    St = w_phys × Sphys  +  w_vis × Svis

Default weights (from config.py):
    w_phys = 0.70   (physiological — glove data)
    w_vis  = 0.30   (visual — DeepFace emotions)

Adaptive weights:
    During active signing, Svis reliability drops (grammar markers
    misread as emotions). Fusion shifts weight toward Sphys.
    The SigningDetector.fusion_shift() method handles this.

Alert classification:
    St ≥ 0.70              → 'high'
    St ≥ 0.45              → 'medium'
    E  ≤ 0.35              → 'disengaged'
    otherwise              → 'normal'

Usage:
    from fusion import FusionEngine

    engine = FusionEngine()
    St, alert = engine.fuse(Sphys, Svis, E, w_phys=0.70, w_vis=0.30)

    # Or with quality-aware weighting (glove offline):
    St, alert = engine.fuse(Sphys, Svis, E,
                             glove_quality=0.0)  # webcam-only fallback
"""

import collections
import numpy as np

from config import (W_PHYS, W_VIS,
                    STRESS_HIGH, STRESS_MED, ENG_LOW,
                    SMOOTHING_WIN)


# ══════════════════════════════════════════════════════════════════════════════
# FUSION ENGINE
# ══════════════════════════════════════════════════════════════════════════════

class FusionEngine:
    """
    Quality-weighted multimodal fusion with temporal smoothing.

    Quality weights:
        glove_quality ∈ [0.0, 1.0]  — 1.0 = glove working, 0.0 = glove offline
        vision_quality ∈ [0.0, 1.0] — 1.0 = face detected, 0.0 = no face

    When glove is offline:
        → w_phys = 0, w_vis = 1.0 (full fallback to webcam)
    When no face detected:
        → w_vis = 0, w_phys = 1.0 (full fallback to glove)
    When both offline:
        → St held at last known value
    """

    def __init__(self, smoothing_win: int = SMOOTHING_WIN):
        self._st_buf    = collections.deque(maxlen=smoothing_win)
        self._sphys_buf = collections.deque(maxlen=smoothing_win)
        self._svis_buf  = collections.deque(maxlen=smoothing_win)
        self._e_buf     = collections.deque(maxlen=smoothing_win)
        self.last_st    = 0.0
        self.last_alert = 'normal'

    def fuse(self,
             sphys: float,
             svis:  float,
             e:     float,
             w_phys: float = W_PHYS,
             w_vis:  float = W_VIS,
             glove_quality:  float = 1.0,
             vision_quality: float = 1.0
             ) -> tuple[float, str]:
        """
        Fuse Sphys + Svis into St and classify alert level.

        Args:
            sphys:           Physiological stress score ∈ [0,1]
            svis:            Visual stress score ∈ [0,1] (after signing suppression)
            e:               Engagement score ∈ [0,1]
            w_phys:          Physiological weight (default from config)
            w_vis:           Visual weight (default from config)
            glove_quality:   1.0 = glove sending data, 0.0 = glove offline
            vision_quality:  1.0 = face detected, 0.0 = no face in frame

        Returns:
            (St, alert)
            St    — fused stress score ∈ [0,1]
            alert — 'normal' | 'medium' | 'high' | 'disengaged'
        """
        # ── Quality-aware weight adjustment ───────────────────────────────────
        eff_phys = w_phys * glove_quality
        eff_vis  = w_vis  * vision_quality
        total    = eff_phys + eff_vis

        if total < 1e-6:
            # Both sources offline — hold last value
            return self.last_st, self.last_alert

        # Renormalise so weights always sum to 1
        eff_phys /= total
        eff_vis  /= total

        # ── Compute St ────────────────────────────────────────────────────────
        self._sphys_buf.append(float(np.clip(sphys, 0.0, 1.0)))
        self._svis_buf.append(float(np.clip(svis,  0.0, 1.0)))
        self._e_buf.append(float(np.clip(e,     0.0, 1.0)))

        sphys_smooth = float(np.mean(self._sphys_buf))
        svis_smooth  = float(np.mean(self._svis_buf))
        e_smooth     = float(np.mean(self._e_buf))

        st_raw = eff_phys * sphys_smooth + eff_vis * svis_smooth
        st     = float(np.clip(st_raw, 0.0, 1.0))

        self._st_buf.append(st)
        st_final = float(np.mean(self._st_buf))

        # ── Alert classification ───────────────────────────────────────────────
        alert = classify_alert(st_final, e_smooth)

        self.last_st    = st_final
        self.last_alert = alert
        return st_final, alert

    @property
    def smoothed_e(self) -> float:
        """Current smoothed engagement score."""
        return float(np.mean(self._e_buf)) if self._e_buf else 0.5

    @property
    def smoothed_sphys(self) -> float:
        """Current smoothed Sphys."""
        return float(np.mean(self._sphys_buf)) if self._sphys_buf else 0.0

    @property
    def smoothed_svis(self) -> float:
        """Current smoothed Svis."""
        return float(np.mean(self._svis_buf)) if self._svis_buf else 0.0


# ══════════════════════════════════════════════════════════════════════════════
# ALERT CLASSIFICATION
# ══════════════════════════════════════════════════════════════════════════════

def classify_alert(st: float, e: float) -> str:
    """
    Map (St, E) to a teacher-facing alert level.

    Priority:
      1. High stress overrides everything
      2. Medium stress
      3. Disengaged (low E regardless of St)
      4. Normal

    Args:
        st: Fused stress score ∈ [0,1]
        e:  Engagement score  ∈ [0,1]

    Returns:
        'high' | 'medium' | 'disengaged' | 'normal'
    """
    if st >= STRESS_HIGH: return 'high'
    if st >= STRESS_MED:  return 'medium'
    if e  <= ENG_LOW:     return 'disengaged'
    return 'normal'


# ══════════════════════════════════════════════════════════════════════════════
# GLOVE FLAT-LINE DETECTOR
# Detects when the glove has stopped sending valid data
# (e.g. sensor disconnected or zero-variance signal)
# ══════════════════════════════════════════════════════════════════════════════

class GloveQualityMonitor:
    """
    Monitors whether the glove is sending physiologically valid data.
    Returns quality ∈ [0.0, 1.0] for use in FusionEngine.

    quality = 1.0  → glove active and varying (healthy signal)
    quality = 0.2  → flat-line detected (no signal variance)
    quality = 0.0  → no data received
    """

    WINDOW = 20   # frames to assess variance over

    def __init__(self):
        self._gsr_buf = collections.deque(maxlen=self.WINDOW)
        self._got_data = False

    def update(self, gsr_norm: float) -> float:
        """
        Update with latest GSR value and return current quality score.

        Args:
            gsr_norm: Normalised GSR value from GSRNormaliser

        Returns:
            quality ∈ [0.0, 1.0]
        """
        self._got_data = True
        self._gsr_buf.append(gsr_norm)

        if len(self._gsr_buf) < self.WINDOW:
            return 1.0   # not enough data yet — assume OK

        std = float(np.std(self._gsr_buf))
        if std < 0.02:
            return 0.2   # flat-line — reduce trust but don't zero out

        return 1.0

    def no_data_quality(self) -> float:
        """Call when glove read fails entirely."""
        return 0.0
