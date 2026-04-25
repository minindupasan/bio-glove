"""
SALE — Environment Controller Simulator
=========================================
Reads latest stress scores from Supabase (stress_scores table),
computes a classroom composite stress, then writes control decisions
to Firebase RTDB at /environment/ every POLL_INTERVAL seconds.

Firebase paths written:
    /environment/ac           — "on" | "off"
    /environment/lighting     — "bright" | "dim" | "normal"
    /environment/fan          — "on" | "off"
    /environment/temp_target  — int °C
    /environment/composite    — float ∈ [0,1]
    /environment/alert_counts — {"high":N,"medium":N,"disengaged":N,"normal":N}
    /environment/ts           — Firebase server timestamp
    /stress/{sid}/stress_level — "high"|"medium"|"disengaged"|"normal"

Usage:
    python env_sim.py
    python env_sim.py --interval 10
"""

import argparse, json, os, sys, time, urllib.request, urllib.error

# ── load .env ────────────────────────────────────────────────────────────────
_env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
if os.path.exists(_env_path):
    for _line in open(_env_path):
        _line = _line.strip()
        if _line and not _line.startswith('#') and '=' in _line:
            _k, _v = _line.split('=', 1)
            os.environ.setdefault(_k.strip(), _v.strip())

SUPABASE_URL = os.environ.get('SUPABASE_URL', '')
SUPABASE_KEY = os.environ.get('SUPABASE_KEY', '')
FIREBASE_DB  = "https://smart-classroom-981e2-default-rtdb.asia-southeast1.firebasedatabase.app"
FIREBASE_AUTH = "mYcjkCxN949mjqC8qbJLeZdO8Y3Iby6DwLTCeLXD"

POLL_INTERVAL = 5   # seconds — matches WRITE_INTERVAL in student.py

STUDENTS = ["S01", "S02", "S03", "S04", "S05", "S06", "S07", "S08"]

# ── Supabase helpers ─────────────────────────────────────────────────────────

def sb_latest_scores() -> dict[str, dict]:
    """Return {sid: {st, sphys, svis, e, alert}} for the most recent row per student."""
    if not SUPABASE_URL:
        return {}
    results = {}
    for sid in STUDENTS:
        try:
            url = (f"{SUPABASE_URL}/rest/v1/stress_scores"
                   f"?student_id=eq.{sid}&order=created_at.desc&limit=1")
            req = urllib.request.Request(url, headers={
                "apikey": SUPABASE_KEY,
                "Authorization": f"Bearer {SUPABASE_KEY}",
            })
            resp = urllib.request.urlopen(req, timeout=3.0)
            rows = json.loads(resp.read().decode())
            if rows:
                results[sid] = rows[0]
        except Exception:
            pass
    return results


# ── Firebase helpers ─────────────────────────────────────────────────────────

def fb_patch(path: str, payload: dict):
    try:
        data = json.dumps(payload).encode()
        url  = f"{FIREBASE_DB}/{path}.json?auth={FIREBASE_AUTH}"
        req  = urllib.request.Request(url, data=data,
                                      headers={"Content-Type": "application/json"},
                                      method="PATCH")
        urllib.request.urlopen(req, timeout=3.0)
    except Exception as e:
        print(f"  [FB] Write failed ({path}): {e}")


# ── Environment decision logic ────────────────────────────────────────────────

def decide_environment(scores: dict[str, dict]) -> dict:
    """
    Derive AC/lighting/fan targets from classroom aggregate stress.

    Rules (simple but explainable):
      composite ≥ 0.65 → AC on, fan on, dim lighting, temp_target 22°C
      composite ≥ 0.45 → AC on, fan off, normal lighting, temp_target 23°C
      else              → AC off, fan off, bright lighting, temp_target 24°C
    """
    if not scores:
        return {
            "ac": "off", "lighting": "normal", "fan": "off",
            "temp_target": 24, "composite": 0.5,
            "alert_counts": {"high": 0, "medium": 0, "disengaged": 0, "normal": 0},
            "ts": {".sv": "timestamp"},
        }

    sts    = [float(v.get('st', 0.5)) for v in scores.values()]
    composite = sum(sts) / len(sts)

    alert_counts = {"high": 0, "medium": 0, "disengaged": 0, "normal": 0}
    for v in scores.values():
        a = v.get('alert', 'normal')
        if a in alert_counts:
            alert_counts[a] += 1

    if composite >= 0.65:
        ac, fan, lighting, temp = "on",  "on",  "dim",    22
    elif composite >= 0.45:
        ac, fan, lighting, temp = "on",  "off", "normal", 23
    else:
        ac, fan, lighting, temp = "off", "off", "bright", 24

    return {
        "ac": ac, "lighting": lighting, "fan": fan,
        "temp_target": temp,
        "composite": round(composite, 4),
        "alert_counts": alert_counts,
        "ts": {".sv": "timestamp"},
    }


# ── Main loop ─────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--interval', type=int, default=POLL_INTERVAL,
                    help='Poll interval in seconds (default 5)')
    args = ap.parse_args()

    if not SUPABASE_URL:
        print("[WARN] SUPABASE_URL not set — stress data will be empty.")
    print("="*55)
    print("  SALE — Environment Controller Simulator")
    print("="*55)
    print(f"  Poll interval : {args.interval}s")
    print(f"  Supabase      : {SUPABASE_URL or '(not configured)'}")
    print(f"  Firebase      : {FIREBASE_DB}/environment/")
    print("  Running — Ctrl+C to quit\n")

    while True:
        scores = sb_latest_scores()
        env    = decide_environment(scores)

        # Write aggregate environment node
        fb_patch("environment", env)

        # Write per-student stress level to /stress/{sid}/
        stress_patch = {}
        for sid, row in scores.items():
            stress_patch[sid.lower()] = {"stress_level": row.get('alert', 'normal')}
        if stress_patch:
            fb_patch("stress", stress_patch)

        composite = env['composite']
        counts    = env['alert_counts']
        print(
            f"  [ENV] composite={composite:.3f}  "
            f"AC={env['ac']} light={env['lighting']} T={env['temp_target']}°C  "
            f"| high={counts['high']} med={counts['medium']} "
            f"diseng={counts['disengaged']} norm={counts['normal']}"
        )

        time.sleep(args.interval)


if __name__ == '__main__':
    main()
