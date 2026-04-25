"""
SALE Component 3 — Physical Stress Module
==========================================
Computes Sphys (physiological stress score) from glove sensor data.

Pipeline:
  1. GSRNormaliser  — per-student z-score, tonic/phasic decomposition,
                      spike removal, Bayesian cold-start adaptation
  2. SVC inference  — predict_proba on normalised features → Sphys ∈ [0,1]

Usage:
    from physical import GSRNormaliser, load_model, predict_sphys

    normaliser = GSRNormaliser(gsr_mu, gsr_sd, volt_mu, volt_sd)
    pipe       = load_model()

    gsr_norm, gsr_tonic, gsr_phasic, gsr_volt_norm = normaliser.process(gsr_kal, gsr_voltage)
    Sphys = predict_sphys(pipe, gsr_norm, gsr_tonic, gsr_phasic,
                          gsr_volt_norm, bpm, skin_temp_c, spo2)
"""

import collections
import os
import pickle

import numpy as np

from config import (MODEL_PATH, NORM_PATH, FEATURES,
                    BPM_MIN, BPM_MAX, SPO2_MIN, TEMP_MIN, TEMP_MAX)


# ══════════════════════════════════════════════════════════════════════════════
# MODEL LOADER
# ══════════════════════════════════════════════════════════════════════════════

def load_model():
    """
    Load trained SVC pipeline from models/stress_model.pkl.
    Returns the sklearn Pipeline object.
    Raises FileNotFoundError if model has not been trained yet.
    """
    if not os.path.exists(MODEL_PATH):
        raise FileNotFoundError(
            f"Model not found at {MODEL_PATH}\n"
            f"Run:  python train.py"
        )
    with open(MODEL_PATH, 'rb') as f:
        bundle = pickle.load(f)
    return bundle['pipeline']


def load_norm_stats(student_id: str) -> tuple[float, float, float, float]:
    """
    Load per-student GSR normalisation parameters from models/norm_stats.csv.
    Falls back to population average if student_id not found.

    Returns: (gsr_mu, gsr_sd, volt_mu, volt_sd)
    """
    import pandas as pd
    if not os.path.exists(NORM_PATH):
        print(f"  [NORM] norm_stats.csv not found — using population average.")
        return 2633.0, 150.0, 2.12, 0.24

    df = pd.read_csv(NORM_PATH).set_index('student_id')
    if student_id in df.index:
        r = df.loc[student_id]
        mu, sd   = float(r['gsr_mean']), float(r['gsr_std'])
        vmu, vsd = float(r['volt_mean']), float(r['volt_std'])
        print(f"  [NORM] Loaded calibration for {student_id}  "
              f"GSR μ={mu:.0f}  σ={sd:.0f}")
        return mu, sd, vmu, vsd
    else:
        print(f"  [NORM] {student_id} not in norm_stats — using population average.")
        print(f"         Sphys accuracy improves after ~3 min as the system adapts.")
        return 2633.0, 150.0, 2.12, 0.24


# ══════════════════════════════════════════════════════════════════════════════
# GSR NORMALISER
# Bayesian cold-start + online Welford update + tonic/phasic decomposition
# ══════════════════════════════════════════════════════════════════════════════

class GSRNormaliser:
    """
    Per-student GSR normaliser with online adaptation.

    Cold-start: uses population prior (from norm_stats.csv) for the first
    WARMUP frames, then gradually shifts to student-specific estimates via
    Welford online update.

    Tonic component  = slow-moving baseline (rolling mean over TONIC_WIN frames)
    Phasic component = fast skin conductance responses (signal − tonic)
    """

    PRIOR_N   = 30    # pseudo-observations from population prior
    TONIC_WIN = 60    # rolling window for tonic baseline (seconds at 1Hz)
    WARMUP    = 30    # frames before estimates stabilise

    def __init__(self, gsr_mu: float, gsr_sd: float,
                 volt_mu: float, volt_sd: float):
        # Population prior (initial values)
        self._mu    = gsr_mu
        self._sd    = max(gsr_sd, 1.0)
        self._vmu   = volt_mu
        self._vsd   = max(volt_sd, 0.01)

        # Welford online stats
        self._n     = 0
        self._mean  = gsr_mu
        self._M2    = (gsr_sd ** 2) * self.PRIOR_N

        # Spike detection
        self._prev      = None
        self._diff_buf  = collections.deque(maxlen=20)

        # Tonic buffer
        self._tonic_buf = collections.deque(maxlen=self.TONIC_WIN)

    def process(self, gsr_kal: float, gsr_voltage: float
                ) -> tuple[float, float, float, float]:
        """
        Process one raw GSR sample.

        Args:
            gsr_kal:     Kalman-filtered GSR raw ADC value
            gsr_voltage: GSR voltage reading

        Returns:
            (gsr_norm, gsr_tonic, gsr_phasic, gsr_volt_norm)
        """
        # ── Spike removal ─────────────────────────────────────────────────────
        if self._prev is not None:
            diff = abs(gsr_kal - self._prev)
            self._diff_buf.append(diff)
            if len(self._diff_buf) > 3:
                gstd = float(np.std(self._diff_buf))
                if gstd > 0 and diff > 2.5 * gstd:
                    gsr_kal = self._prev   # replace spike with previous value
        self._prev = gsr_kal

        # ── Welford online update ─────────────────────────────────────────────
        self._n += 1
        n       = self._n + self.PRIOR_N
        delta   = gsr_kal - self._mean
        self._mean += delta / n
        self._M2   += delta * (gsr_kal - self._mean)

        if self._n >= self.WARMUP:
            self._mu = self._mean
            self._sd = max(float(np.sqrt(self._M2 / max(n - 1, 1))), 1.0)

        # ── Z-score normalisation ─────────────────────────────────────────────
        gsr_norm      = (gsr_kal      - self._mu)  / self._sd
        gsr_volt_norm = (gsr_voltage  - self._vmu) / self._vsd

        # ── Tonic / phasic decomposition ──────────────────────────────────────
        self._tonic_buf.append(gsr_norm)
        tonic  = float(np.mean(self._tonic_buf))
        phasic = gsr_norm - tonic

        return float(gsr_norm), float(tonic), float(phasic), float(gsr_volt_norm)


# ══════════════════════════════════════════════════════════════════════════════
# SVC INFERENCE
# ══════════════════════════════════════════════════════════════════════════════

def predict_sphys(pipe,
                  gsr_norm: float, gsr_tonic: float, gsr_phasic: float,
                  gsr_volt_norm: float,
                  bpm: float, skin_temp_c: float, spo2: float) -> float:
    """
    Run SVC-RBF inference on one frame of normalised features.

    Args:
        pipe:          Trained sklearn Pipeline (StandardScaler + SVC)
        gsr_norm:      Normalised GSR signal
        gsr_tonic:     Tonic component of GSR
        gsr_phasic:    Phasic component of GSR
        gsr_volt_norm: Normalised GSR voltage
        bpm:           Heart rate (clipped to physiological range)
        skin_temp_c:   Skin temperature in Celsius
        spo2:          Blood oxygen saturation

    Returns:
        Sphys ∈ [0.0, 1.0] — probability of stress class
    """
    # Feature order must match FEATURES in config.py:
    # ["GSR_NORM","GSR_TONIC","GSR_PHASIC","GSR_VOLT_NORM","BPM","SKIN_TEMP_C","SPO2"]
    x = np.array([[gsr_norm, gsr_tonic, gsr_phasic, gsr_volt_norm,
                   np.clip(bpm,       BPM_MIN,  BPM_MAX),
                   np.clip(skin_temp_c, TEMP_MIN, TEMP_MAX),
                   np.clip(spo2,      SPO2_MIN, 100.0)]])
    return float(pipe.predict_proba(x)[0, 1])
