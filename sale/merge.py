"""
SALE Component 3 — Dataset Merger
====================================
Merges newly collected CSVs from data_collected/ with the
original combined_classroom_dataset.csv, then re-runs training.

Usage:
    python merge.py                      # merge all new files
    python merge.py --preview            # show what would be merged, no changes
    python merge.py --retrain            # merge + immediately retrain model

What it does:
    1. Finds all CSV files in data_collected/
    2. Validates column structure matches the original dataset
    3. Re-IDs students to avoid clashes (e.g. S09, S10, ...)
    4. Merges into a single combined CSV
    5. Prints summary of the merged dataset
    6. Optionally retrains the model on the merged data
"""

import argparse
import os
import sys
from datetime import datetime

import pandas as pd
import numpy as np

# ── Paths ──────────────────────────────────────────────────────────────────────
ROOT           = os.path.dirname(os.path.abspath(__file__))
ORIGINAL_CSV   = os.path.join(ROOT, "data_collected", "original",
                               "combined_classroom_dataset.csv")
COLLECTED_DIR  = os.path.join(ROOT, "data_collected")
MERGED_CSV     = os.path.join(ROOT, "data_collected", "merged_dataset.csv")

REQUIRED_COLS = ['timestamp','student_id','label','BPM','SPO2',
                 'GSR_RAW','GSR_KAL','GSR_VOLTAGE','IR','RED',
                 'SKIN_TEMP_C','SKIN_TEMP_F']

def find_new_files() -> list:
    """Find all CSVs in data_collected/ (excluding merged output and originals)."""
    skip = {os.path.basename(MERGED_CSV), 'combined_classroom_dataset.csv'}
    files = []
    for f in os.listdir(COLLECTED_DIR):
        if f.endswith('.csv') and f not in skip:
            files.append(os.path.join(COLLECTED_DIR, f))
    return sorted(files)

def validate(df: pd.DataFrame, path: str) -> bool:
    missing = [c for c in REQUIRED_COLS if c not in df.columns]
    if missing:
        print(f"  [SKIP] {os.path.basename(path)} — missing columns: {missing}")
        return False
    if df['label'].isnull().any():
        print(f"  [WARN] {os.path.basename(path)} — NaN labels found, rows dropped")
    return True

def assign_new_student_ids(df: pd.DataFrame,
                           existing_ids: set,
                           file_id_map: dict) -> pd.DataFrame:
    """
    Give each student in new data a unique ID that doesn't clash
    with existing dataset student IDs.
    Maps original IDs (e.g. S09) → non-clashing IDs if needed.
    """
    df = df.copy()
    for orig_id in df['student_id'].unique():
        if orig_id in file_id_map:
            df.loc[df['student_id']==orig_id, 'student_id'] = file_id_map[orig_id]
        elif orig_id not in existing_ids:
            file_id_map[orig_id] = orig_id   # keep as-is
        else:
            # Clash — generate a new ID
            n = max(int(s[1:]) for s in existing_ids if s[1:].isdigit()) + 1
            new_id = f"S{n:02d}"
            existing_ids.add(new_id)
            file_id_map[orig_id] = new_id
            print(f"  [REMAP] {orig_id} → {new_id} (clash avoided)")
            df.loc[df['student_id']==orig_id, 'student_id'] = new_id
    return df

def load_original() -> pd.DataFrame | None:
    """Load the original dataset if it exists."""
    # Try several common paths
    candidates = [
        ORIGINAL_CSV,
        os.path.join(ROOT, "data_collected", "combined_classroom_dataset.csv"),
        r"D:\MSC\Stress Module\Classroom_Dataset\combined_classroom_dataset.csv",
    ]
    for path in candidates:
        if os.path.exists(path):
            df = pd.read_csv(path)
            print(f"  Original dataset: {path}  ({len(df):,} rows)")
            return df
    return None

def print_summary(df: pd.DataFrame, title: str = "Dataset"):
    print(f"\n  {title}")
    print(f"  {'─'*50}")
    print(f"  Total rows   : {len(df):,}")
    print(f"  Students     : {sorted(df['student_id'].unique())}")
    print(f"  Label 0      : {(df['label']==0).sum():,}  ({(df['label']==0).mean()*100:.1f}%)")
    print(f"  Label 1      : {(df['label']==1).sum():,}  ({(df['label']==1).mean()*100:.1f}%)")
    print(f"  Date range   : {df['timestamp'].min()} → {df['timestamp'].max()}")
    print()

def main():
    ap = argparse.ArgumentParser(description='SALE Dataset Merger')
    ap.add_argument('--preview',  action='store_true', help='Preview only, do not write')
    ap.add_argument('--retrain',  action='store_true', help='Retrain model after merge')
    ap.add_argument('--original', default=None,        help='Path to original dataset CSV')
    args = ap.parse_args()

    print("=" * 55)
    print("  SALE — Dataset Merger")
    print("=" * 55)

    # ── Load original ──────────────────────────────────────────────────────────
    orig_df = load_original()
    if args.original:
        orig_df = pd.read_csv(args.original)
        print(f"  Original: {args.original}  ({len(orig_df):,} rows)")

    if orig_df is None:
        print("  [INFO] No original dataset found — merging new files only.")
        print(f"         To include original data, copy combined_classroom_dataset.csv")
        print(f"         to:  {os.path.join(COLLECTED_DIR, 'original/')}")
        existing_ids = set()
        frames = []
    else:
        existing_ids = set(orig_df['student_id'].unique())
        frames = [orig_df]

    # ── Find new files ─────────────────────────────────────────────────────────
    new_files = find_new_files()

    if not new_files:
        print("\n  No new files found in data_collected/")
        print(f"  Run:  python collect.py --student S09 --demo")
        return

    print(f"\n  New files found: {len(new_files)}")
    for f in new_files:
        print(f"    {os.path.basename(f)}")

    # ── Load and validate each new file ───────────────────────────────────────
    print()
    file_id_map = {}
    new_rows    = 0

    for path in new_files:
        df = pd.read_csv(path)
        if not validate(df, path): continue

        df = df.dropna(subset=['label'])
        df['label'] = df['label'].astype(int)
        df = assign_new_student_ids(df, existing_ids, file_id_map)

        n    = len(df)
        sids = sorted(df['student_id'].unique())
        l0   = (df['label']==0).sum()
        l1   = (df['label']==1).sum()
        dur  = n / 60.0

        print(f"  {os.path.basename(path)}")
        print(f"    Students: {sids}  |  Rows: {n}  |  Duration: {dur:.1f} min")
        print(f"    Label 0: {l0}  Label 1: {l1}  Balance: {l0/max(l1,1):.2f}")

        frames.append(df)
        new_rows += n

    if not frames:
        print("\n  No valid files to merge.")
        return

    # ── Merge ──────────────────────────────────────────────────────────────────
    merged = pd.concat(frames, ignore_index=True)
    merged = merged.sort_values(['student_id','timestamp']).reset_index(drop=True)

    print_summary(merged, "Merged Dataset")

    if args.preview:
        print("  [PREVIEW] No files written (--preview mode).")
        return

    # ── Save ───────────────────────────────────────────────────────────────────
    os.makedirs(os.path.dirname(MERGED_CSV), exist_ok=True)
    merged.to_csv(MERGED_CSV, index=False)
    print(f"  Saved: {MERGED_CSV}")

    # ── Update config.py to point at merged dataset ───────────────────────────
    config_path = os.path.join(ROOT, 'config.py')
    with open(config_path) as f:
        config_src = f.read()

    if MERGED_CSV.replace('\\', '/') not in config_src.replace('\\', '/'):
        print(f"\n  [NOTE] Update DATASET_PATH in config.py to use the merged dataset:")
        print(f"         DATASET_PATH = r\"{MERGED_CSV}\"")

    print(f"\n  New data added  : {new_rows:,} rows")
    if orig_df is not None:
        print(f"  Original data   : {len(orig_df):,} rows")
    print(f"  Total merged    : {len(merged):,} rows")
    print(f"  Total students  : {merged['student_id'].nunique()}")

    # ── Retrain ────────────────────────────────────────────────────────────────
    if args.retrain:
        print(f"\n  Retraining model on merged dataset...")
        # Temporarily patch config to use merged CSV
        import importlib, config as cfg_mod
        cfg_mod.DATASET_PATH = MERGED_CSV
        import train
        train.main_with_path(MERGED_CSV)
    else:
        print(f"\n  To retrain with merged data:")
        print(f"    1. Set DATASET_PATH = r\"{MERGED_CSV}\" in config.py")
        print(f"    2. Run: python train.py")

    print()


if __name__ == '__main__':
    main()
