"""
SALE Component 3 — Model Training
===================================
Runs the full training pipeline on the classroom dataset.
Output: models/stress_model.pkl + models/norm_stats.csv

Usage:
    python train.py
"""

import os, sys, time, pickle, warnings
import numpy as np
import pandas as pd
import matplotlib; matplotlib.use('Agg')
import matplotlib.pyplot as plt
import seaborn as sns
from sklearn.svm import SVC
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import Pipeline
from sklearn.metrics import (accuracy_score, f1_score, roc_auc_score,
                             classification_report)
warnings.filterwarnings('ignore')

from config import (DATASET_PATH, MODEL_PATH, NORM_PATH, MODELS_DIR,
                    FEATURES, STUDENTS, TONIC_WINDOW,
                    BPM_MIN, BPM_MAX, SPO2_MIN, GSR_MIN, GSR_MAX,
                    TEMP_MIN, TEMP_MAX, SVC_C, SVC_GAMMA, ensure_dirs)

def log(msg): print(f"  {msg}")

# ══════════════════════════════════════════════════════════════════════════════
# PHASE 1 — Load & clean
# ══════════════════════════════════════════════════════════════════════════════
def load_and_clean(path: str) -> pd.DataFrame:
    print("\n[1/3] Loading and cleaning dataset...")

    if not os.path.exists(path):
        raise FileNotFoundError(
            f"Dataset not found: {path}\n"
            f"Edit DATASET_PATH in config.py"
        )

    df = pd.read_csv(path)
    df['timestamp'] = pd.to_datetime(df['timestamp'], format='%Y.%m.%d %H:%M:%S')
    df = df.sort_values(['student_id','timestamp']).reset_index(drop=True)
    log(f"Loaded {len(df):,} rows  ·  {df['student_id'].nunique()} students")

    # Fill warm-up NaNs (MAX30102 sensor)
    fill = ['BPM','SPO2','IR','RED']
    df[fill] = df.groupby('student_id')[fill].transform(lambda x: x.ffill().bfill())

    # Clip physiological bounds
    df['BPM']         = df['BPM'].clip(BPM_MIN, BPM_MAX)
    df['SPO2']        = df['SPO2'].clip(SPO2_MIN, 100.0)
    df['GSR_RAW']     = df['GSR_RAW'].clip(GSR_MIN, GSR_MAX)
    df['GSR_KAL']     = df['GSR_KAL'].clip(GSR_MIN, GSR_MAX)
    df['SKIN_TEMP_C'] = df['SKIN_TEMP_C'].clip(TEMP_MIN, TEMP_MAX)
    df['SKIN_TEMP_F'] = df['SKIN_TEMP_C'] * 9/5 + 32

    # GSR spike flag (rapid-change detection)
    df['GSR_SPIKE'] = 0
    for s in df['student_id'].unique():
        idx  = df[df['student_id']==s].index
        gsr  = df.loc[idx,'GSR_KAL'].values.astype(float)
        diff = np.abs(np.diff(gsr, prepend=gsr[0]))
        df.loc[idx,'GSR_SPIKE'] = (diff > 2.5 * diff.std()).astype(int)

    log(f"Cleaned  ·  label 0: {(df.label==0).sum():,}  label 1: {(df.label==1).sum():,}")
    return df


# ══════════════════════════════════════════════════════════════════════════════
# PHASE 2 — GSR normalisation
# ══════════════════════════════════════════════════════════════════════════════
def normalise_gsr(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    print("\n[2/3] GSR normalisation pipeline...")

    # Spike interpolation
    df['GSR_INTERP'] = df['GSR_KAL'].astype(float)
    for s in df['student_id'].unique():
        idx  = df[df['student_id']==s].index
        vals = df.loc[idx,'GSR_KAL'].astype(float).values.copy()
        vals[df.loc[idx,'GSR_SPIKE'].values.astype(bool)] = np.nan
        df.loc[idx,'GSR_INTERP'] = (pd.Series(vals)
                                      .interpolate(method='linear', limit_direction='both')
                                      .values)

    # Per-student z-score
    stats_rows = []
    df['GSR_NORM'] = 0.0; df['GSR_VOLT_NORM'] = 0.0
    for s in df['student_id'].unique():
        mask = df['student_id']==s
        g    = df.loc[mask,'GSR_INTERP']
        v    = df.loc[mask,'GSR_VOLTAGE']
        mu_g, sd_g = g.mean(), max(g.std(), 1e-6)
        mu_v, sd_v = v.mean(), max(v.std(), 1e-6)
        df.loc[mask,'GSR_NORM']      = (g - mu_g) / sd_g
        df.loc[mask,'GSR_VOLT_NORM'] = (v - mu_v) / sd_v
        stats_rows.append({'student_id':s,'gsr_mean':round(mu_g,2),'gsr_std':round(sd_g,2),
                           'volt_mean':round(mu_v,4),'volt_std':round(sd_v,4)})
        log(f"{s}  μ={mu_g:.0f}  σ={sd_g:.0f}  →  GSR_NORM μ≈0")

    # Tonic / phasic decomposition
    df['GSR_TONIC'] = 0.0; df['GSR_PHASIC'] = 0.0
    for s in df['student_id'].unique():
        mask  = df['student_id']==s
        norm  = df.loc[mask,'GSR_NORM']
        tonic = norm.rolling(TONIC_WINDOW, center=True, min_periods=1).mean()
        df.loc[mask,'GSR_TONIC']  = tonic.values
        df.loc[mask,'GSR_PHASIC'] = (norm - tonic).values

    # 1–99th pct clip (removes SCR outliers)
    for col in ['GSR_NORM','GSR_TONIC','GSR_PHASIC']:
        lo, hi = df[col].quantile(0.01), df[col].quantile(0.99)
        df[col] = df[col].clip(lo, hi)

    norm_df = pd.DataFrame(stats_rows)
    return df, norm_df


# ══════════════════════════════════════════════════════════════════════════════
# PHASE 3 — SVC-RBF training + LOSO evaluation
# ══════════════════════════════════════════════════════════════════════════════
def train_model(df: pd.DataFrame, norm_df: pd.DataFrame) -> Pipeline:
    print(f"\n[3/3] Training SVC-RBF (C={SVC_C}, gamma={SVC_GAMMA})...")
    print(f"      Evaluation: Leave-One-Student-Out cross-validation")
    print(f"      This takes 8–15 minutes depending on your machine.\n")

    df = df.dropna(subset=FEATURES+['label'])
    students = df['student_id'].unique()
    n_students = len(students)
    rows = []
    all_yt, all_yp, all_prob = [], [], []

    t0 = time.time()
    for fold_i, s in enumerate(students, 1):
        # Progress bar
        done  = '█' * fold_i
        left  = '░' * (n_students - fold_i)
        pct   = int(fold_i / n_students * 100)
        elapsed = time.time() - t0
        eta = (elapsed / fold_i) * (n_students - fold_i) if fold_i > 1 else 0
        print(f"      [{done}{left}] {pct:3d}%  fold {fold_i}/{n_students}  "
              f"testing {s}  elapsed {elapsed:.0f}s  ETA {eta:.0f}s", end='\r')

        tr = df[df['student_id']!=s]; te = df[df['student_id']==s]
        pipe = Pipeline([
            ('sc',  StandardScaler()),
            ('svc', SVC(kernel='rbf', C=SVC_C, gamma=SVC_GAMMA,
                        class_weight='balanced', probability=True, random_state=42))
        ])
        pipe.fit(tr[FEATURES].values, tr['label'].values)
        yp = pipe.predict(te[FEATURES].values)
        yb = pipe.predict_proba(te[FEATURES].values)[:,1]
        yt = te['label'].values

        acc = accuracy_score(yt,yp); f1 = f1_score(yt,yp,average='weighted')
        auc = roc_auc_score(yt,yb)
        rows.append({'student':s,'acc':acc,'f1':f1,'auc':auc})
        all_yt.extend(yt.tolist()); all_yp.extend(yp.tolist()); all_prob.extend(yb.tolist())
        print(f"\n      [{s}]  Acc={acc:.3f}  F1={f1:.3f}  AUC={auc:.3f}")

    res = pd.DataFrame(rows)
    mean_acc = res['acc'].mean(); mean_f1 = res['f1'].mean(); mean_auc = res['auc'].mean()

    print(f"\n      ┌──────────────────────────────────────────┐")
    print(f"      │  LOSO Results (n={len(students)} students)             │")
    print(f"      │  Accuracy : {mean_acc:.4f} ± {res['acc'].std():.4f}              │")
    print(f"      │  F1       : {mean_f1:.4f}                        │")
    print(f"      │  AUC-ROC  : {mean_auc:.4f}                        │")
    print(f"      │  Time     : {time.time()-t0:.1f}s                          │")
    print(f"      └──────────────────────────────────────────┘")

    # Feature sensitivity
    final = Pipeline([
        ('sc',  StandardScaler()),
        ('svc', SVC(kernel='rbf', C=SVC_C, gamma=SVC_GAMMA,
                    class_weight='balanced', probability=True, random_state=42))
    ])
    final.fit(df[FEATURES].values, df['label'].values)
    base = final.predict_proba(df[FEATURES].values)[:,1]
    sens = {}
    for i,f in enumerate(FEATURES):
        Xp = df[FEATURES].values.copy()
        np.random.seed(42); Xp[:,i] = np.random.permutation(Xp[:,i])
        sens[f] = float(np.mean(np.abs(base - final.predict_proba(Xp)[:,1])))

    print(f"\n      Top features:")
    for k,v in sorted(sens.items(), key=lambda x:-x[1])[:3]:
        print(f"        {k:<20} sensitivity={v:.4f}")

    # Save model
    ensure_dirs()
    with open(MODEL_PATH,'wb') as fh:
        pickle.dump({'pipeline':final,'features':FEATURES,
                     'loso_acc':mean_acc,'loso_f1':mean_f1,'loso_auc':mean_auc,
                     'sensitivity':sens}, fh)
    norm_df.to_csv(NORM_PATH, index=False)

    print(f"\n      Saved: {MODEL_PATH}")
    print(f"      Saved: {NORM_PATH}")

    # Quick plot
    _save_results_plot(res, all_yt, all_prob)

    return final


def _save_results_plot(res, all_yt, all_prob):
    from sklearn.metrics import roc_curve
    fig, axes = plt.subplots(1, 2, figsize=(12, 4), facecolor='#FAFAFA')
    fig.suptitle('SALE — Model Evaluation (LOSO)', fontweight='bold')

    ax = axes[0]
    colors = ['#4C72B0','#DD8452','#55A868','#C44E52','#8172B3','#937860','#DA8BC3','#8C8C8C']
    bars = ax.bar(res['student'], res['acc'], color=colors[:len(res)], edgecolor='white', width=0.6)
    for b,v in zip(bars, res['acc']):
        ax.text(b.get_x()+b.get_width()/2, b.get_height()+0.005, f'{v:.3f}', ha='center', fontsize=9)
    ax.axhline(res['acc'].mean(), color='black', ls='--', lw=1.2, label=f"Mean={res['acc'].mean():.3f}")
    ax.set_ylim(0,1.1); ax.set_title('LOSO Accuracy per Student', fontweight='bold')
    ax.legend(frameon=False); ax.spines[['top','right']].set_visible(False)

    ax2 = axes[1]
    fpr, tpr, _ = roc_curve(all_yt, all_prob)
    ax2.plot(fpr, tpr, color='#4C72B0', lw=2, label=f"AUC={roc_auc_score(all_yt,all_prob):.3f}")
    ax2.fill_between(fpr, tpr, alpha=0.08, color='#4C72B0')
    ax2.plot([0,1],[0,1],'k--',lw=0.8,alpha=0.5)
    ax2.set_title('ROC Curve', fontweight='bold')
    ax2.legend(frameon=False); ax2.spines[['top','right']].set_visible(False)

    out = os.path.join(MODELS_DIR, 'training_results.png')
    plt.tight_layout(); plt.savefig(out, dpi=130, bbox_inches='tight', facecolor='#FAFAFA')
    plt.close()
    print(f"      Plot: {out}")


# ══════════════════════════════════════════════════════════════════════════════
if __name__ == '__main__':
    print("=" * 55)
    print("  SALE — Model Training Pipeline")
    print("=" * 55)
    t_start = time.time()

    df                = load_and_clean(DATASET_PATH)
    df, norm_df       = normalise_gsr(df)
    train_model(df, norm_df)

    print(f"\n{'='*55}")
    print(f"  Done in {time.time()-t_start:.0f}s  →  models/ ready")
    print(f"  Next: python student.py")
    print(f"{'='*55}\n")
