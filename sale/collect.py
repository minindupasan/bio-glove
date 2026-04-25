"""
SALE Component 3 — Data Collection Tool
=========================================
Use this at a deaf school to collect new labelled data.
Output CSV matches combined_classroom_dataset.csv format exactly
so it can be merged directly with the existing dataset.

Usage:
    python collect.py --student S09 --port COM3
    python collect.py --student S09 --port COM3 --session baseline
    python collect.py --student S09 --demo          # no glove, test run

Controls during collection:
    SPACE  — toggle label  (0 = baseline / 1 = stress)
    Q/ESC  — stop and save
    R      — reset current segment (discard since last label change)

Output:
    data_collected/S09_2026-04-14_10-30.csv
    (same columns as combined_classroom_dataset.csv)
"""

import argparse
import csv
import os
import sys
import time
import threading
import collections
from datetime import datetime

import cv2
import numpy as np

# ── Config ─────────────────────────────────────────────────────────────────────
BAUD_RATE    = 115200
FRAME_W      = 800
FRAME_H      = 600
OUTPUT_DIR   = "data_collected"

# Column order must match combined_classroom_dataset.csv exactly
CSV_COLUMNS = [
    'timestamp', 'student_id', 'label',
    'BPM', 'SPO2', 'GSR_RAW', 'GSR_KAL', 'GSR_VOLTAGE',
    'IR', 'RED', 'SKIN_TEMP_C', 'SKIN_TEMP_F'
]

C = dict(
    green  = (80,  200, 80),
    red    = (60,  60,  220),
    amber  = (0,   200, 220),
    white  = (255, 255, 255),
    dark   = (20,  20,  20),
)


# ══════════════════════════════════════════════════════════════════════════════
# GLOVE READER
# ══════════════════════════════════════════════════════════════════════════════
class GloveSerial:
    """Read from real ESP32 glove over USB serial."""
    def __init__(self, port: str):
        import serial
        self._ser  = serial.Serial(port, BAUD_RATE, timeout=2)
        self._data = {}
        self._lock = threading.Lock()
        threading.Thread(target=self._loop, daemon=True).start()
        print(f"  [GLOVE] Connected on {port}")

    def _loop(self):
        while True:
            try:
                line   = self._ser.readline().decode('utf-8', errors='ignore').strip()
                parts  = line.split(',')
                if len(parts) < 9: continue
                # Format: BPM,SpO2,GSR_RAW,GSR_KAL,GSR_VOLTAGE,IR,RED,SKIN_TEMP_C,SKIN_TEMP_F
                with self._lock:
                    self._data = {
                        'BPM':         parts[0],
                        'SPO2':        parts[1],
                        'GSR_RAW':     parts[2],
                        'GSR_KAL':     parts[3],
                        'GSR_VOLTAGE': parts[4],
                        'IR':          parts[5],
                        'RED':         parts[6],
                        'SKIN_TEMP_C': parts[7],
                        'SKIN_TEMP_F': parts[8],
                    }
            except Exception:
                time.sleep(0.1)

    def read(self) -> dict:
        with self._lock:
            return dict(self._data)


class GloveDemo:
    """Synthetic glove data for test runs without hardware."""
    def __init__(self):
        self._t = 0.0

    def read(self) -> dict:
        self._t += 1.0; t = self._t
        bpm      = 70 + 6*np.sin(t*.3) + np.random.randn()*.8
        spo2     = 97.5 + .5*np.sin(t*.1)
        gsr_raw  = int(2650 + 180*np.sin(t*.2) + np.random.randn()*10)
        gsr_kal  = int(2650 + 170*np.sin(t*.2) + np.random.randn()*5)
        gsr_v    = 2.14 + 0.15*np.sin(t*.2)
        ir       = int(52000 + np.random.randn()*100)
        red      = int(43000 + np.random.randn()*100)
        temp_c   = 33.2 + 0.3*np.sin(t*.05)
        temp_f   = temp_c * 9/5 + 32
        return {
            'BPM':         f"{bpm:.1f}",
            'SPO2':        f"{spo2:.1f}",
            'GSR_RAW':     str(gsr_raw),
            'GSR_KAL':     str(gsr_kal),
            'GSR_VOLTAGE': f"{gsr_v:.4f}",
            'IR':          str(ir),
            'RED':         str(red),
            'SKIN_TEMP_C': f"{temp_c:.2f}",
            'SKIN_TEMP_F': f"{temp_f:.2f}",
        }


# ══════════════════════════════════════════════════════════════════════════════
# OVERLAY HELPERS
# ══════════════════════════════════════════════════════════════════════════════
def _panel(img, x, y, w, h, alpha=0.70):
    ov = img.copy()
    cv2.rectangle(ov, (x,y), (x+w,y+h), C['dark'], -1)
    cv2.addWeighted(ov, alpha, img, 1-alpha, 0, img)
    cv2.rectangle(img, (x,y), (x+w,y+h), (60,65,80), 1)

def _txt(img, text, x, y, scale=0.45, color=None, bold=False):
    cv2.putText(img, text, (x,y),
                cv2.FONT_HERSHEY_SIMPLEX, scale,
                color or C['white'], 2 if bold else 1, cv2.LINE_AA)


# ══════════════════════════════════════════════════════════════════════════════
# MAIN COLLECTION LOOP
# ══════════════════════════════════════════════════════════════════════════════
def main():
    ap = argparse.ArgumentParser(description='SALE Data Collection')
    ap.add_argument('--student', required=True, help='Student ID, e.g. S09')
    ap.add_argument('--port',    default=None,  help='Serial port (COM3 / /dev/ttyUSB0)')
    ap.add_argument('--session', default='',    help='Optional session label (e.g. baseline, task1)')
    ap.add_argument('--demo',    action='store_true', help='Run without glove')
    ap.add_argument('--cam',     type=int, default=0, help='Webcam index')
    args = ap.parse_args()

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    print("=" * 55)
    print("  SALE — Data Collection Tool")
    print("=" * 55)
    print(f"  Student  : {args.student}")
    print(f"  Glove    : {'demo' if args.demo else args.port}")
    print(f"  Camera   : index {args.cam}")
    print()
    print("  Controls:")
    print("    SPACE  — toggle label (0=baseline / 1=stress)")
    print("    Q/ESC  — stop and save CSV")
    print("    R      — discard current segment")
    print("=" * 55)

    # Glove
    glove = GloveDemo() if (args.demo or not args.port) else GloveSerial(args.port)

    # Camera (optional — used for visual feedback only, not saved)
    cap = cv2.VideoCapture(args.cam)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_W)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_H)
    cam_ok = cap.isOpened()
    if not cam_ok:
        print("  [INFO] Camera not found — running without video preview.")

    # Output file
    ts_str   = datetime.now().strftime('%Y-%m-%d_%H-%M')
    sess_str = f"_{args.session}" if args.session else ""
    out_path = os.path.join(OUTPUT_DIR, f"{args.student}{sess_str}_{ts_str}.csv")
    out_f    = open(out_path, 'w', newline='')
    writer   = csv.DictWriter(out_f, fieldnames=CSV_COLUMNS)
    writer.writeheader()
    print(f"\n  Saving to: {out_path}\n")

    # State
    current_label   = 0   # 0 = baseline, 1 = stress
    rows_written    = 0
    label_0_rows    = 0
    label_1_rows    = 0
    segment_start   = time.time()
    segment_rows    = 0
    pending_discard = False
    last_glove      = {}
    glove_ok        = False
    start_t         = time.time()
    fps             = 0.0
    fps_t           = time.time()

    print("  Ready. Press SPACE to start labelling as STRESSED.")
    print("  Data recording starts now...\n")

    frame_n = 0
    while True:
        # ── Read glove every second ───────────────────────────────────────────
        if frame_n % 30 == 0 or not last_glove:
            raw = glove.read()
            if raw:
                last_glove = raw
                glove_ok   = True

        # ── Write CSV row ─────────────────────────────────────────────────────
        if last_glove and not pending_discard:
            row = {
                'timestamp':  datetime.now().strftime('%Y.%m.%d %H:%M:%S'),
                'student_id': args.student,
                'label':      current_label,
            }
            row.update(last_glove)
            writer.writerow(row)
            out_f.flush()

            rows_written  += 1
            segment_rows  += 1
            if current_label == 0: label_0_rows += 1
            else:                  label_1_rows += 1

        # ── Camera frame ──────────────────────────────────────────────────────
        if cam_ok:
            ret, frame = cap.read()
            if not ret:
                frame = np.zeros((FRAME_H, FRAME_W, 3), dtype=np.uint8)
        else:
            frame = np.zeros((FRAME_H, FRAME_W, 3), dtype=np.uint8)

        frame_n += 1
        h, w = frame.shape[:2]

        # FPS
        now  = time.time()
        fps  = 0.9*fps + 0.1/(max(now-fps_t, 1e-6))
        fps_t = now

        # ── Status panel ──────────────────────────────────────────────────────
        elapsed  = int(now - start_t)
        seg_dur  = int(now - segment_start)
        mins, sc = divmod(elapsed, 60)

        _panel(frame, 0, 0, w, 46)

        if current_label == 0:
            label_color = C['green']
            label_text  = "BASELINE  (label 0)"
        else:
            label_color = C['red']
            label_text  = "STRESSED  (label 1)"

        _txt(frame, f"SALE | {args.student} | {mins:02d}:{sc:02d} | FPS:{fps:.0f}",
             10, 20, 0.50, C['white'])

        # Big label badge
        _panel(frame, 10, 56, 340, 50)
        _txt(frame, label_text, 20, 90, 0.80, label_color, bold=True)
        _txt(frame, "SPACE to toggle", 210, 90, 0.42, (160,160,160))

        # Stats panel (right)
        _panel(frame, w-240, 56, 230, 160)
        _txt(frame, "Collection Stats", w-228, 76, 0.45, C['amber'])
        _txt(frame, f"Total rows  : {rows_written}", w-228, 98,  0.40)
        _txt(frame, f"Baseline    : {label_0_rows} rows ({label_0_rows//60:.1f} min)", w-228, 116, 0.40)
        _txt(frame, f"Stressed    : {label_1_rows} rows ({label_1_rows//60:.1f} min)", w-228, 134, 0.40)
        _txt(frame, f"Segment dur : {seg_dur}s", w-228, 152, 0.40)
        _txt(frame, f"Glove       : {'OK' if glove_ok else 'waiting...'}", w-228, 170,
             0.40, C['green'] if glove_ok else C['amber'])
        _txt(frame, f"Saving to   : {os.path.basename(out_path)}", w-228, 194, 0.35, (140,140,140))

        # Balance warning
        if rows_written > 200:
            ratio = label_1_rows / max(label_0_rows, 1)
            if ratio < 0.4 or ratio > 2.5:
                _txt(frame, "⚠  Label imbalance — toggle more often", 10, h-14,
                     0.40, C['amber'])
            else:
                _txt(frame, "Q/ESC=save+quit  |  R=discard segment", 10, h-14,
                     0.38, (100,100,120))
        else:
            _txt(frame, "Q/ESC=save+quit  |  R=discard segment", 10, h-14,
                 0.38, (100,100,120))

        cv2.imshow(f"SALE — Collecting {args.student}", frame)
        time.sleep(0.033)   # ~30fps

        # ── Key handling ──────────────────────────────────────────────────────
        key = cv2.waitKey(1) & 0xFF

        if key == ord(' '):
            current_label = 1 - current_label
            segment_start = time.time()
            segment_rows  = 0
            pending_discard = False
            state_str = "STRESSED" if current_label == 1 else "BASELINE"
            print(f"  [{datetime.now().strftime('%H:%M:%S')}] → Label changed to {current_label} ({state_str})")

        elif key == ord('r') or key == ord('R'):
            # Discard current segment — seek back in CSV to remove last segment_rows
            pending_discard = True
            out_f.flush()
            # Rewrite CSV without last segment
            out_f.close()
            _discard_last_segment(out_path, segment_rows)
            out_f    = open(out_path, 'a', newline='')
            writer   = csv.DictWriter(out_f, fieldnames=CSV_COLUMNS)
            rows_written  -= segment_rows
            if current_label == 0: label_0_rows -= segment_rows
            else:                  label_1_rows -= segment_rows
            segment_rows  = 0
            pending_discard = False
            print(f"  [{datetime.now().strftime('%H:%M:%S')}] Segment discarded.")

        elif key in (ord('q'), 27):
            print(f"\n  Stopping...")
            break

    # ── Save & summary ────────────────────────────────────────────────────────
    cap.release(); cv2.destroyAllWindows()
    out_f.close()

    print(f"\n{'='*55}")
    print(f"  Collection complete")
    print(f"{'='*55}")
    print(f"  Student      : {args.student}")
    print(f"  Total rows   : {rows_written}")
    print(f"  Baseline (0) : {label_0_rows} rows  ({label_0_rows/60:.1f} min)")
    print(f"  Stressed (1) : {label_1_rows} rows  ({label_1_rows/60:.1f} min)")
    balance = label_0_rows / max(label_1_rows, 1)
    print(f"  Balance      : {balance:.2f}  {'✓ good' if 0.5 <= balance <= 2.0 else '⚠ skewed'}")
    print(f"  Saved to     : {out_path}")
    print(f"{'='*55}\n")
    print(f"  Next step: python merge.py")


def _discard_last_segment(path: str, n_rows: int):
    """Remove the last n_rows from the CSV file."""
    with open(path, 'r') as f:
        lines = f.readlines()
    keep = lines[:max(1, len(lines) - n_rows)]  # always keep header
    with open(path, 'w') as f:
        f.writelines(keep)


if __name__ == '__main__':
    main()
