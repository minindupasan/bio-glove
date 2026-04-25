"""
SALE Component 3 — Student Monitor
=====================================
Runs on the student laptop. Captures webcam + glove data, computes
multimodal stress and engagement, posts to Supabase every 5s.

Modules:
    physical.py   → GSRNormaliser, load_model, predict_sphys  (Sphys)
    emotion.py    → EmotionDetector, SigningDetector           (Svis)
    engagement.py → EngagementEstimator                       (E)
    fusion.py     → FusionEngine, GloveQualityMonitor         (St, alert)

Usage:
    python student.py --id S01
    python student.py --id S01 --cam 1
    python student.py --id S01 --port COM3    (real glove)
"""

import argparse, json, os, sys, threading, time, warnings
import cv2, mediapipe as mp, numpy as np, urllib.request, urllib.error

warnings.filterwarnings('ignore')

from config import (NORM_PATH, CAM_INDEX, FRAME_W, FRAME_H,
                    WRITE_INTERVAL, FIREBASE_DB, FIREBASE_AUTH,
                    STRESS_HIGH, STRESS_MED, ENG_LOW)

# ── SALE modules ──────────────────────────────────────────────────────────────
from physical   import GSRNormaliser, load_model, load_norm_stats, predict_sphys
from emotion    import EmotionDetector, SigningDetector
from engagement import EngagementEstimator
from fusion     import FusionEngine, GloveQualityMonitor


# ══════════════════════════════════════════════════════════════════════════════
# GLOVE SOURCES
# ══════════════════════════════════════════════════════════════════════════════
class GloveDemo:
    """Smooth synthetic glove data for demo / development."""
    def __init__(self): self._t = 0.0
    def read(self) -> dict:
        self._t += 1.0; t = self._t
        from config import BPM_MIN, BPM_MAX, SPO2_MIN, TEMP_MIN, TEMP_MAX
        return {
            'bpm':         float(np.clip(70+6*np.sin(t*.3)+np.random.randn()*.8, BPM_MIN, BPM_MAX)),
            'spo2':        float(np.clip(97.5+.5*np.sin(t*.1), SPO2_MIN, 100)),
            'gsr_kal':     float(2650 + 180*np.sin(t*.2) + np.random.randn()*8),
            'gsr_voltage': float(2.14 + 0.15*np.sin(t*.2)),
            'skin_temp_c': float(np.clip(33.2+.3*np.sin(t*.05), TEMP_MIN, TEMP_MAX)),
        }

class GloveFirebase:
    """Polls /glove/{sid} from Firebase RTDB. Use --source firebase to activate."""
    _DB  = "https://smart-classroom-981e2-default-rtdb.asia-southeast1.firebasedatabase.app"
    _AUTH = "mYcjkCxN949mjqC8qbJLeZdO8Y3Iby6DwLTCeLXD"

    def __init__(self, sid: str):
        self._url  = f"{self._DB}/glove/{sid.lower()}.json?auth={self._AUTH}"
        self._last: dict = {}
        print(f"  [GLOVE] Firebase source: /glove/{sid.lower()}")

    def read(self) -> dict:
        from config import BPM_MIN, BPM_MAX, SPO2_MIN, TEMP_MIN, TEMP_MAX
        try:
            req  = urllib.request.Request(self._url)
            resp = urllib.request.urlopen(req, timeout=3.0)
            data = json.loads(resp.read().decode())
            if not data:
                return self._last or self._defaults()
            self._last = {
                'bpm':         float(np.clip(float(data.get('bpm', 70)),        BPM_MIN,  BPM_MAX)),
                'spo2':        float(np.clip(float(data.get('spo2', 98)),        SPO2_MIN, 100)),
                'gsr_kal':     float(data.get('gsr_kal', 2650)),
                'gsr_voltage': float(data.get('gsr_voltage', 2.14)),
                'skin_temp_c': float(np.clip(float(data.get('skin_temp_c', 33)), TEMP_MIN, TEMP_MAX)),
            }
            return self._last
        except Exception as e:
            print(f"  [GLOVE] Firebase read failed: {e}")
            return self._last or self._defaults()

    @staticmethod
    def _defaults() -> dict:
        return {'bpm': 70.0, 'spo2': 98.0, 'gsr_kal': 2650.0,
                'gsr_voltage': 2.14, 'skin_temp_c': 33.0}


class GloveSerial:
    """Real ESP32 glove via USB serial. Use --port COM3 to activate."""
    def __init__(self, port: str, baud: int = 115200):
        import serial
        self._ser = serial.Serial(port, baud, timeout=2)
        print(f"  [GLOVE] Connected on {port}")
    def read(self) -> dict:
        from config import BPM_MIN, BPM_MAX, SPO2_MIN, TEMP_MIN, TEMP_MAX
        line  = self._ser.readline().decode('utf-8', errors='ignore').strip()
        parts = line.split(',')
        # ESP32 format: BPM,SpO2,GSR_RAW,GSR_KAL,GSR_VOLTAGE,IR,RED,SKIN_TEMP_C,SKIN_TEMP_F
        return {
            'bpm':         float(np.clip(float(parts[0]), BPM_MIN, BPM_MAX)),
            'spo2':        float(np.clip(float(parts[1]), SPO2_MIN, 100)),
            'gsr_kal':     float(parts[3]),
            'gsr_voltage': float(parts[4]),
            'skin_temp_c': float(np.clip(float(parts[7]), 30, 38)),
        }


# ══════════════════════════════════════════════════════════════════════════════
# FIREBASE WRITER
# ══════════════════════════════════════════════════════════════════════════════
def _fb_patch(path: str, payload: dict):
    try:
        data = json.dumps(payload).encode()
        url  = f"{FIREBASE_DB}/{path}.json?auth={FIREBASE_AUTH}"
        req  = urllib.request.Request(url, data=data,
                                      headers={"Content-Type": "application/json"},
                                      method="PATCH")
        urllib.request.urlopen(req, timeout=2.0)
    except Exception as e:
        print(f"  [FB] Write failed ({path}): {e}")

def db_write(sid, sphys, svis, st, e, alert, bpm, spo2,
             gsr_norm, gsr_tonic, gsr_phasic, skin_temp,
             gesture=None, is_signing=False, emotions=None):
    sid_lower = sid.lower()

    # Stress + sensor data → /students/{sid}/
    threading.Thread(daemon=True, target=_fb_patch, args=(f"students/{sid_lower}", {
        'sphys': round(float(sphys), 4), 'svis': round(float(svis), 4),
        'st': round(float(st), 4), 'e': round(float(e), 4), 'alert': alert,
        'bpm': round(float(bpm), 1), 'spo2': round(float(spo2), 1),
        'gsr_norm': round(float(gsr_norm), 4), 'skin_temp_c': round(float(skin_temp), 2),
        'ts': {'.sv': 'timestamp'},
    })).start()

    # Engagement → /engagement/{sid}/  (dashboard reads this)
    eng_score  = int(round(float(e) * 100))
    eng_status = "ENGAGED" if float(e) > ENG_LOW else "Disengaged"
    threading.Thread(daemon=True, target=_fb_patch, args=(f"engagement/{sid_lower}", {
        'engagement_score': eng_score,
        'engagement_status': eng_status,
    })).start()

    # Sign gesture → /sign/{sid}/  (dashboard reads this)
    if gesture:
        threading.Thread(daemon=True, target=_fb_patch, args=(f"sign/{sid_lower}", {
            'label': gesture,
            'confidence': 1.0,
        })).start()

    # Emotion → /emotion/{sid}/  (dominant label + full probabilities)
    if emotions:
        dominant = max(emotions, key=emotions.get)
        total    = sum(emotions.values()) + 1e-9
        probs    = {k: round(v / total, 4) for k, v in emotions.items()}
        threading.Thread(daemon=True, target=_fb_patch, args=(f"emotion/{sid_lower}", {
            'emotion_label': dominant,
            'stress_score':  round(float(svis), 4),
            'probabilities': probs,
            'ts':            {'.sv': 'timestamp'},
        })).start()


# ══════════════════════════════════════════════════════════════════════════════
# OVERLAY HELPERS
# ══════════════════════════════════════════════════════════════════════════════
def _panel(img, x, y, w, h, alpha=0.65):
    ov = img.copy()
    cv2.rectangle(ov, (x, y), (x+w, y+h), (18,18,24), -1)
    cv2.addWeighted(ov, alpha, img, 1-alpha, 0, img)
    cv2.rectangle(img, (x, y), (x+w, y+h), (55,60,75), 1)

def _bar(img, x, y, w, h, val, col, label, val_str):
    cv2.rectangle(img, (x,y), (x+w,y+h), (40,40,40), -1)
    cv2.rectangle(img, (x,y), (x+int(val*w),y+h), col, -1)
    cv2.rectangle(img, (x,y), (x+w,y+h), (80,80,80), 1)
    cv2.putText(img, label,   (x,y-5),   cv2.FONT_HERSHEY_SIMPLEX, .37, (255,255,255), 1, cv2.LINE_AA)
    cv2.putText(img, val_str, (x+w+5,y+h-1), cv2.FONT_HERSHEY_SIMPLEX, .37, (255,255,255), 1, cv2.LINE_AA)

def _sc(v, inv=False):
    if inv: v = 1-v
    return (0, int(min(255, 2*(1-v)*255)), int(min(255, 2*v*255)))


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--id',     default='S01')
    ap.add_argument('--cam',    type=int, default=CAM_INDEX)
    ap.add_argument('--port',   default=None, help='Serial port for real glove')
    ap.add_argument('--source', default='firebase',
                    choices=['demo', 'serial', 'firebase'],
                    help='Glove data source: firebase (default) | serial | demo')
    args = ap.parse_args()

    print("="*55)
    print(f"  SALE — Student Monitor  [{args.id}]")
    print("="*55)
    src_label = (f'Serial {args.port}' if args.source == 'serial'
                 else f'Firebase /glove/{args.id.lower()}' if args.source == 'firebase'
                 else 'Demo (synthetic)')
    print(f"  Glove  : {src_label}")
    print(f"  Camera : index {args.cam}")

    # ── Load model + norm stats ───────────────────────────────────────────────
    pipe = load_model()
    gsr_mu, gsr_sd, volt_mu, volt_sd = load_norm_stats(args.id)

    # ── Initialise modules ────────────────────────────────────────────────────
    if args.source == 'serial' or args.port:
        glove = GloveSerial(args.port)
    elif args.source == 'firebase':
        glove = GloveFirebase(args.id)
    else:
        glove = GloveDemo()
    normaliser = GSRNormaliser(gsr_mu, gsr_sd, volt_mu, volt_sd)   # physical.py
    quality    = GloveQualityMonitor()                              # fusion.py
    signing    = SigningDetector()                                  # emotion.py
    detector   = EmotionDetector()                                  # emotion.py
    detector.start_preload()                                        # preload DeepFace

    # Camera + MediaPipe
    cap = cv2.VideoCapture(args.cam)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_W)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_H)
    if not cap.isOpened():
        print("[ERROR] Camera not found."); sys.exit(1)

    face_mesh = mp.solutions.face_mesh.FaceMesh(
        max_num_faces=1, refine_landmarks=True,
        min_detection_confidence=0.5, min_tracking_confidence=0.5)

    estimator  = EngagementEstimator(cam_w=FRAME_W, cam_h=FRAME_H)  # engagement.py
    engine     = FusionEngine()                                      # fusion.py

    print("  Running — press Q to quit\n")

    fn = 0; fps = 0.; fps_t = time.time()
    last_glove = glove.read(); last_write = 0.

    while True:
        ret, frame = cap.read()
        if not ret: time.sleep(0.05); continue
        fn += 1; h, w = frame.shape[:2]
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

        # ── Glove (update ~1Hz; Firebase polls on same cadence) ──────────────
        if fn % 30 == 0:
            last_glove = glove.read()
        g = last_glove
        bpm   = float(g['bpm']); spo2  = float(g['spo2'])
        skin  = float(g['skin_temp_c'])

        # physical.py — normalise GSR
        gsr_norm, gsr_tonic, gsr_phasic, gsr_volt_norm = \
            normaliser.process(g['gsr_kal'], g['gsr_voltage'])

        # physical.py — SVC inference → Sphys
        Sphys = predict_sphys(pipe, gsr_norm, gsr_tonic, gsr_phasic,
                               gsr_volt_norm, bpm, skin, spo2)
        glove_q = quality.update(gsr_norm)

        # ── MediaPipe ─────────────────────────────────────────────────────────
        mp_res = face_mesh.process(rgb)
        face_ok = bool(mp_res.multi_face_landmarks)
        lm      = mp_res.multi_face_landmarks[0].landmark if face_ok else None

        # emotion.py — signing detection + Svis
        signing.update(frame, lm, w, h)
        detector.submit_frame(frame)
        Svis_raw, emotions = detector.get_result()
        Svis = Svis_raw * signing.svis_multiplier()

        # engagement.py — E
        if face_ok:
            estimator.update(lm, is_signing=signing.is_signing)
            estimator.draw_landmarks(frame, lm)
        E = estimator.last_score

        # fusion.py — St + alert
        w_phys, w_vis = signing.fusion_shift()
        St, alert = engine.fuse(
            Sphys, Svis, E,
            w_phys=w_phys, w_vis=w_vis,
            glove_quality=glove_q,
            vision_quality=1.0 if face_ok else 0.0
        )

        # ── Supabase write (every WRITE_INTERVAL seconds) ─────────────────────
        now = time.time()
        if now - last_write >= WRITE_INTERVAL:
            db_write(args.id, Sphys, Svis, St, E, alert,
                     bpm, spo2, gsr_norm, gsr_tonic, gsr_phasic, skin,
                     gesture=signing.last_gesture if hasattr(signing, 'last_gesture') else None,
                     is_signing=signing.is_signing,
                     emotions=emotions if emotions else None)
            last_write = now
            sign_tag = " [SIGNING]" if signing.is_signing else ""
            print(f"  [DB] {args.id}  St={St:.3f}  "
                  f"Sphys={Sphys:.3f}  Svis={Svis:.3f}  "
                  f"E={E:.3f}  {alert.upper()}{sign_tag}")

        # FPS
        n = time.time(); fps = .9*fps + .1/(max(n-fps_t, 1e-6)); fps_t = n

        # ── Overlay ───────────────────────────────────────────────────────────
        _panel(frame, 0, 0, w, 40)
        cv2.putText(frame,
            f"SALE | {args.id} | FPS:{fps:.0f} | "
            f"{'face' if face_ok else 'no face'} | "
            f"{'SIGNING' if signing.is_signing else 'idle'} | Q=quit",
            (10, 26), cv2.FONT_HERSHEY_SIMPLEX, .45, (255,255,255), 1, cv2.LINE_AA)

        px, py, pw = w-268, 50, 252; _panel(frame, px, py, pw, 290)
        y = py + 22

        def txt(s, col=(255,255,255)):
            nonlocal y
            cv2.putText(frame, s, (px+10, y),
                        cv2.FONT_HERSHEY_SIMPLEX, .36, col, 1, cv2.LINE_AA)
            y += 13

        txt("── PHYSICAL STRESS ──"); y += 2
        _bar(frame, px+10, y, pw-60, 13, Sphys, _sc(Sphys),
             "Sphys (glove→SVC)", f"{Sphys:.2f}"); y += 32

        txt("── EMOTION STRESS ──"); y += 2
        _bar(frame, px+10, y, pw-60, 13, Svis, _sc(Svis),
             "Svis  (DeepFace)",  f"{Svis:.2f}"); y += 32

        txt("── FUSED STRESS ──"); y += 2
        _bar(frame, px+10, y, pw-60, 17, St, _sc(St),
             "St  fusion",       f"{St:.2f}"); y += 38

        cv2.line(frame,(px+10,y),(px+pw-10,y),(55,60,75),1); y += 8
        txt("── ENGAGEMENT ──"); y += 2
        _bar(frame, px+10, y, pw-60, 13, E, _sc(E, True),
             "E   (MediaPipe)",  f"{E:.2f}"); y += 30

        cv2.line(frame,(px+10,y),(px+pw-10,y),(55,60,75),1); y += 8
        txt(f"BPM:{bpm:.0f}  SpO2:{spo2:.0f}%  T:{skin:.1f}°C", (0,180,220))
        txt(f"GSR_N:{gsr_norm:.2f}  Tonic:{gsr_tonic:.2f}", (0,180,220))
        txt(f"Yaw:{estimator.last_yaw:+.0f}°  Pitch:{estimator.last_pitch:+.0f}°",
            (220,140,50))

        if St >= STRESS_HIGH: bc,bt = (60,60,220),"HIGH STRESS"
        elif St >= STRESS_MED: bc,bt = (0,180,220),"CAUTION"
        else: bc,bt = (80,200,80),"NORMAL"
        _panel(frame, 10, 50, 200, 38)
        cv2.putText(frame, bt, (18, 78),
                    cv2.FONT_HERSHEY_SIMPLEX, .68, bc, 2, cv2.LINE_AA)

        rem = max(0, WRITE_INTERVAL - (time.time()-last_write))
        cv2.putText(frame, f"DB write in {rem:.0f}s",
                    (10, h-8), cv2.FONT_HERSHEY_SIMPLEX,
                    .34, (80,80,100), 1, cv2.LINE_AA)

        cv2.imshow(f"SALE — {args.id}", frame)
        key = cv2.waitKey(1) & 0xFF
        if key in (ord('q'), 27):
            break

    face_mesh.close(); cap.release(); cv2.destroyAllWindows()
    print(f"\n  [DONE] {fn} frames processed.")


if __name__ == '__main__':
    main()
