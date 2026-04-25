"""
SALE — Environment Controller Simulator
=========================================
Reads latest stress scores from Firebase RTDB (/students/),
computes a classroom composite stress, then writes control decisions
back to Firebase at /environment/ every POLL_INTERVAL seconds.

Firebase paths read:
    /students/{sid}/st        — fused stress score [0,1]
    /students/{sid}/alert     — alert level

Firebase paths written:
    /environment/ac           — "ON" | "OFF"
    /environment/ac_setpoint  — int °C
    /environment/lighting     — "bright" | "normal" | "dim"
    /environment/fan          — "ON" | "OFF"
    /environment/composite    — float ∈ [0,1]
    /environment/temp         — simulated room temp (°C)
    /environment/humidity     — simulated humidity (%)
    /environment/alert_counts — {"high":N,"medium":N,"disengaged":N,"normal":N}
    /environment/ts           — Firebase server timestamp

Usage:
    python env_sim.py
    python env_sim.py --interval 10
"""

import argparse, json, math, os, random, time, urllib.request, urllib.error

FIREBASE_DB   = "https://smart-classroom-981e2-default-rtdb.asia-southeast1.firebasedatabase.app"
FIREBASE_AUTH = "mYcjkCxN949mjqC8qbJLeZdO8Y3Iby6DwLTCeLXD"

POLL_INTERVAL = 5
STUDENTS      = ["s01", "s02", "s03", "s04", "s05"]

# Simulated room environment (drifts slowly like a real classroom)
_sim_temp     = 26.0
_sim_humidity = 62.0


# ── Firebase helpers ──────────────────────────────────────────────────────────

def fb_get(path):
    try:
        url  = f"{FIREBASE_DB}/{path}.json?auth={FIREBASE_AUTH}"
        req  = urllib.request.Request(url)
        resp = urllib.request.urlopen(req, timeout=3.0)
        return json.loads(resp.read().decode())
    except Exception as e:
        print(f"  [FB] Read failed ({path}): {e}")
        return None

def fb_patch(path, payload):
    try:
        data = json.dumps(payload).encode()
        url  = f"{FIREBASE_DB}/{path}.json?auth={FIREBASE_AUTH}"
        req  = urllib.request.Request(url, data=data,
                                      headers={"Content-Type": "application/json"},
                                      method="PATCH")
        urllib.request.urlopen(req, timeout=3.0)
    except Exception as e:
        print(f"  [FB] Write failed ({path}): {e}")


# ── Read all students from Firebase ──────────────────────────────────────────

def read_student_scores() -> dict:
    """Returns {sid: {st, alert}} for each student that has data."""
    data = fb_get("students")
    if not data:
        return {}
    scores = {}
    for sid in STUDENTS:
        row = data.get(sid)
        if row and 'st' in row:
            scores[sid] = {'st': float(row['st']), 'alert': row.get('alert', 'normal')}
    return scores


# ── Simulated DHT22 sensor ────────────────────────────────────────────────────

def sim_environment(ac_on: bool, ac_setpoint: int) -> tuple:
    """Slowly drift simulated room temp and humidity."""
    global _sim_temp, _sim_humidity
    target_temp = ac_setpoint if ac_on else 28.0
    _sim_temp    += (target_temp - _sim_temp) * 0.05 + random.uniform(-0.05, 0.05)
    _sim_humidity += (65.0 - _sim_humidity)  * 0.02 + random.uniform(-0.2, 0.2)
    _sim_temp     = max(18.0, min(35.0, _sim_temp))
    _sim_humidity = max(40.0, min(90.0, _sim_humidity))
    return round(_sim_temp, 1), round(_sim_humidity, 1)


# ── Environment decision logic ────────────────────────────────────────────────

def decide_environment(scores: dict) -> dict:
    """
    Derive AC/lighting/fan targets from classroom aggregate stress.

      composite >= 0.65 → AC ON,  fan ON,  dim lighting,    22°C
      composite >= 0.45 → AC ON,  fan OFF, normal lighting,  23°C
      else              → AC OFF, fan OFF, bright lighting,  24°C
    """
    if not scores:
        composite = 0.0
        alert_counts = {"high": 0, "medium": 0, "disengaged": 0, "normal": 0}
        ac, fan, lighting, setpoint = "OFF", "OFF", "bright", 24
    else:
        sts = [v['st'] for v in scores.values()]
        composite = sum(sts) / len(sts)
        alert_counts = {"high": 0, "medium": 0, "disengaged": 0, "normal": 0}
        for v in scores.values():
            a = v.get('alert', 'normal')
            if a in alert_counts:
                alert_counts[a] += 1

        if composite >= 0.65:
            ac, fan, lighting, setpoint = "ON",  "ON",  "dim",    22
        elif composite >= 0.45:
            ac, fan, lighting, setpoint = "ON",  "OFF", "normal", 23
        else:
            ac, fan, lighting, setpoint = "OFF", "OFF", "bright", 24

    temp, humidity = sim_environment(ac == "ON", setpoint)

    return {
        "ac":           ac,
        "ac_setpoint":  setpoint,
        "lighting":     lighting,
        "fan":          fan,
        "composite":    round(composite, 4),
        "temp":         temp,
        "humidity":     humidity,
        "alert_counts": alert_counts,
        "ts":           {".sv": "timestamp"},
    }


# ── Main loop ─────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--interval', type=int, default=POLL_INTERVAL)
    args = ap.parse_args()

    print("=" * 55)
    print("  SALE — Environment Controller Simulator")
    print("=" * 55)
    print(f"  Source   : Firebase /students/")
    print(f"  Output   : Firebase /environment/")
    print(f"  Interval : {args.interval}s")
    print("  Running — Ctrl+C to quit\n")

    while True:
        scores = read_student_scores()
        env    = decide_environment(scores)

        fb_patch("environment", env)

        counts = env['alert_counts']
        print(
            f"  [ENV] composite={env['composite']:.3f}  "
            f"AC={env['ac']} ({env['ac_setpoint']}°C)  "
            f"light={env['lighting']}  "
            f"T={env['temp']}°C  H={env['humidity']}%  "
            f"| high={counts['high']} med={counts['medium']} "
            f"diseng={counts['disengaged']} norm={counts['normal']}"
        )

        time.sleep(args.interval)


if __name__ == '__main__':
    main()
