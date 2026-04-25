"""
SALE Component 3 — Configuration
Reads secrets from .env file — never hardcode keys in source code.
"""
import os

ROOT = os.path.dirname(os.path.abspath(__file__))

# ── Load .env file if present ──────────────────────────────────────────────────
_env_path = os.path.join(ROOT, '.env')
if os.path.exists(_env_path):
    for _line in open(_env_path):
        _line = _line.strip()
        if _line and not _line.startswith('#') and '=' in _line:
            _k, _v = _line.split('=', 1)
            os.environ.setdefault(_k.strip(), _v.strip())

# ── Dataset ────────────────────────────────────────────────────────────────────
DATASET_PATH = os.environ.get(
    'DATASET_PATH',
    r"D:\MSC\Stress Module\Classroom_Dataset\combined_classroom_dataset.csv"
)

# ── Model paths ────────────────────────────────────────────────────────────────
MODELS_DIR = os.path.join(ROOT, "models")
MODEL_PATH = os.path.join(MODELS_DIR, "stress_model.pkl")
NORM_PATH  = os.path.join(MODELS_DIR, "norm_stats.csv")

# ── Supabase — loaded from .env, never hardcoded ───────────────────────────────
SUPABASE_URL = os.environ.get('SUPABASE_URL', '')
SUPABASE_KEY = os.environ.get('SUPABASE_KEY', '')

if not SUPABASE_URL or not SUPABASE_KEY:
    print("[WARN] Supabase credentials not found in .env — DB writes disabled.")

# ── Physiological bounds ───────────────────────────────────────────────────────
BPM_MIN, BPM_MAX   = 40, 120
SPO2_MIN           = 90
GSR_MIN, GSR_MAX   = 100, 4090
TEMP_MIN, TEMP_MAX = 30.0, 38.0

# ── ML ─────────────────────────────────────────────────────────────────────────
FEATURES = ["GSR_NORM","GSR_TONIC","GSR_PHASIC","GSR_VOLT_NORM",
            "BPM","SKIN_TEMP_C","SPO2"]
STUDENTS = ["S01","S02","S03","S04","S05","S06","S07","S08"]
SVC_C, SVC_GAMMA = 10, "scale"
TONIC_WINDOW     = 60

# ── Fusion ─────────────────────────────────────────────────────────────────────
W_PHYS, W_VIS = 0.70, 0.30
STRESS_HIGH   = 0.70
STRESS_MED    = 0.45
ENG_LOW       = 0.35

# ── Runtime ────────────────────────────────────────────────────────────────────
CAM_INDEX         = 0
FRAME_W, FRAME_H  = 1280, 720
DEEPFACE_INTERVAL = 10
SMOOTHING_WIN     = 15
WRITE_INTERVAL    = 5

def ensure_dirs():
    os.makedirs(MODELS_DIR, exist_ok=True)
