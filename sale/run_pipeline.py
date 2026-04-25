"""
SALE — Pipeline Runner
=======================
Starts simulate.py (S02–S05) and env_sim.py together.
Press Ctrl+C to stop both cleanly.

Run student.py separately on the student's machine:
    python student.py --id S01 --source firebase

Usage:
    python run_pipeline.py
    python run_pipeline.py --interval 10
"""

import argparse, subprocess, sys, time, os, signal

HERE = os.path.dirname(os.path.abspath(__file__))
PY   = sys.executable

COMPONENTS = [
    ("simulate ", [PY, os.path.join(HERE, "simulate.py")]),
    ("env_sim  ", [PY, os.path.join(HERE, "env_sim.py")]),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=int, default=5,
                    help="Poll interval for env_sim (default 5s)")
    args = ap.parse_args()

    procs = []
    print("=" * 55)
    print("  SALE — Pipeline Runner")
    print("=" * 55)

    for label, cmd in COMPONENTS:
        if "env_sim" in cmd[-1]:
            cmd = cmd + ["--interval", str(args.interval)]
        p = subprocess.Popen(cmd, cwd=HERE)
        procs.append((label, p))
        print(f"  ▶  {label}  (pid {p.pid})")
        time.sleep(0.5)   # stagger startup

    print()
    print("  All components running. Press Ctrl+C to stop.\n")
    print("  NOTE: Run student.py separately:")
    print("        python student.py --id S01 --source firebase")
    print()

    try:
        while True:
            # Restart any component that crashes unexpectedly
            for i, (label, p) in enumerate(procs):
                ret = p.poll()
                if ret is not None:
                    print(f"  [WARN] {label} exited (code {ret}) — restarting...")
                    cmd = COMPONENTS[i][1]
                    if "env_sim" in cmd[-1]:
                        cmd = cmd + ["--interval", str(args.interval)]
                    new_p = subprocess.Popen(cmd, cwd=HERE)
                    procs[i] = (label, new_p)
                    print(f"  ▶  {label}  restarted (pid {new_p.pid})")
            time.sleep(3)

    except KeyboardInterrupt:
        print("\n  Stopping all components...")
        for label, p in procs:
            p.terminate()
        # Give them a moment to exit cleanly
        time.sleep(1)
        for label, p in procs:
            if p.poll() is None:
                p.kill()
            print(f"  ■  {label}  stopped")
        print("\n  Pipeline stopped.")


if __name__ == "__main__":
    main()
