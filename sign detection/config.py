"""
Configuration for Sign Language Recognition System.
All hyperparameters and paths in one place.
"""
import os

# ──────────────────────────────────────────────
# Paths
# ──────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "Data")
MODEL_DIR = os.path.join(BASE_DIR, "models")
PREPROCESSED_DATA_PATH = os.path.join(BASE_DIR, "preprocessed_data.npz")

# ──────────────────────────────────────────────
# MediaPipe
# ──────────────────────────────────────────────
MP_STATIC_IMAGE_MODE = False
MP_MIN_DETECTION_CONFIDENCE = 0.5
MP_MIN_TRACKING_CONFIDENCE = 0.5

# Landmark counts
NUM_POSE_LANDMARKS = 33     # x, y, z, visibility → we use x, y, z
NUM_HAND_LANDMARKS = 21     # x, y, z per hand (left + right)
FEATURES_PER_LANDMARK = 3   # x, y, z
TOTAL_FEATURES = (NUM_POSE_LANDMARKS + 2 * NUM_HAND_LANDMARKS) * FEATURES_PER_LANDMARK  # (33 + 42) * 3 = 225

# ──────────────────────────────────────────────
# Preprocessing
# ──────────────────────────────────────────────
SEQUENCE_LENGTH = 30         # Number of frames per sample
MAX_VIDEO_FRAMES = 90        # Skip videos longer than this (likely corrupted)

# Augmentation
AUGMENT_NOISE_STD = 0.005    # Gaussian noise added to landmarks
AUGMENT_TEMPORAL_JITTER = 3  # Max frames to shift temporally
NUM_AUGMENTED_COPIES = 5     # How many augmented copies per original sample

# ──────────────────────────────────────────────
# Model
# ──────────────────────────────────────────────
LSTM_UNITS_1 = 256
LSTM_UNITS_2 = 128
DENSE_UNITS = 128
DROPOUT_RATE = 0.3
LEARNING_RATE = 0.001
BATCH_SIZE = 32
EPOCHS = 100
EARLY_STOP_PATIENCE = 15
REDUCE_LR_PATIENCE = 7
REDUCE_LR_FACTOR = 0.5
VALIDATION_SPLIT = 0.2

# ──────────────────────────────────────────────
# Real-time Inference
# ──────────────────────────────────────────────
CONFIDENCE_THRESHOLD = 0.5   # Only show predictions above this
PREDICTION_SMOOTHING = 5     # Average over N predictions for stability
WEBCAM_WIDTH = 1280
WEBCAM_HEIGHT = 720
