"""
SALE Component 3 — Student Simulator
=======================================
Simulates S02–S05 posting realistic stress/engagement data to Supabase.
Run this alongside student.py (which handles S01 with the real webcam).

Usage:
    python simulate.py

Student profiles:
  S02  Amara   — calm, high engagement, occasional mild stress
  S03  Nimal   — high stress (exam-anxiety type), moderate engagement
  S04  Thisara — low engagement, moderate stress
  S05  Kasuni  — balanced, normal across both dimensions
"""

import json, math, threading, time, urllib.request
import numpy as np
from config import SUPABASE_URL, SUPABASE_KEY, STRESS_HIGH, STRESS_MED, ENG_LOW

WRITE_INTERVAL = 5  # seconds

def classify(st, e):
    if st >= STRESS_HIGH: return 'high'
    if st >= STRESS_MED:  return 'medium'
    if e  <= ENG_LOW:     return 'disengaged'
    return 'normal'

def post(payload):
    try:
        data = json.dumps(payload).encode()
        req  = urllib.request.Request(
            f"{SUPABASE_URL}/rest/v1/stress_scores", data=data,
            headers={"apikey":SUPABASE_KEY,"Authorization":f"Bearer {SUPABASE_KEY}",
                     "Content-Type":"application/json","Prefer":"return=minimal"},
            method="POST")
        urllib.request.urlopen(req, timeout=2.0)
        return True
    except Exception as e:
        print(f"  [WARN] {e}"); return False


class Student:
    def __init__(self, sid, name, base_st, amp_st, period_st,
                 base_e, amp_e, period_e, spike_prob=0.05, spike_mag=0.22):
        self.sid=sid; self.name=name
        self.base_st=base_st; self.amp_st=amp_st; self.period_st=period_st
        self.base_e=base_e;   self.amp_e=amp_e;   self.period_e=period_e
        self.spike_prob=spike_prob; self.spike_mag=spike_mag
        self._t  = np.random.uniform(0,100)
        self._sp = 0.; self._dp = 0.

    def tick(self):
        self._t += WRITE_INTERVAL/60.

        st = self.base_st + self.amp_st*math.sin(2*math.pi*self._t/self.period_st)
        e  = self.base_e  - self.amp_e *math.sin(2*math.pi*self._t/self.period_e)

        if np.random.random() < self.spike_prob: self._sp = self.spike_mag
        if np.random.random() < self.spike_prob*0.7: self._dp = self.spike_mag*0.6
        self._sp = max(0., self._sp-0.04)
        self._dp = max(0., self._dp-0.03)

        st = float(np.clip(st+self._sp+np.random.randn()*.025, 0,1))
        e  = float(np.clip(e -self._dp+np.random.randn()*.020, 0,1))
        return st, e

    def run(self):
        print(f"  [{self.sid}] {self.name:10s} started  base_St={self.base_st:.2f}  base_E={self.base_e:.2f}")
        while True:
            st, e = self.tick()
            sphys = float(np.clip(st+np.random.randn()*.04, 0,1))
            svis  = float(np.clip(st*.4+np.random.randn()*.05, 0,1))
            st_f  = round(.70*sphys+.30*svis, 4)
            al    = classify(st_f, e)
            ok    = post({'student_id':self.sid,'sphys':round(sphys,4),
                          'svis':round(svis,4),'st':st_f,'e':round(e,4),'alert':al})
            ts    = time.strftime('%H:%M:%S')
            print(f"  [{self.sid}] {ts}  St={st_f:.3f}  E={e:.3f}  {al.upper():<12}  {'OK' if ok else 'FAIL'}")
            time.sleep(WRITE_INTERVAL)


PROFILES = [
    Student('S02','Amara',   0.28,.10,8.0,  0.72,.08,10.0, 0.04,0.22),
    Student('S03','Nimal',   0.62,.14,5.0,  0.52,.12, 7.0, 0.10,0.20),
    Student('S04','Thisara', 0.38,.09,9.0,  0.31,.10, 6.0, 0.06,0.15),
    Student('S05','Kasuni',  0.33,.11,12.0, 0.66,.09, 9.0, 0.04,0.18),
]

if __name__ == '__main__':
    print("="*55)
    print("  SALE — Student Simulator (S02–S05)")
    print("="*55)
    threads = [threading.Thread(target=s.run, daemon=True) for s in PROFILES]
    for i,t in enumerate(threads): t.start(); time.sleep(0.4)
    print()
    try:
        while True: time.sleep(1)
    except KeyboardInterrupt:
        print("\n  [STOP] Simulator stopped.")
