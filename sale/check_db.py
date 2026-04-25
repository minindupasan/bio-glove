"""
Quick DB health check — run from sale/ with venv active:
    python3 check_db.py
"""
import json, os, urllib.request

for line in open(os.path.join(os.path.dirname(__file__), '.env')):
    line = line.strip()
    if line and not line.startswith('#') and '=' in line:
        k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())

URL = os.environ['SUPABASE_URL']
KEY = os.environ['SUPABASE_KEY']

TABLES = ['stress_scores', 'sensor_readings', 'alert_log']

def sb_get(table, params=''):
    req = urllib.request.Request(
        f'{URL}/rest/v1/{table}?{params}',
        headers={
            'apikey': KEY,
            'Authorization': f'Bearer {KEY}',
            'Accept': 'application/json',
        }
    )
    try:
        resp = urllib.request.urlopen(req, timeout=5)
        return json.loads(resp.read()), None
    except urllib.error.HTTPError as e:
        return None, f'HTTP {e.code}: {e.read().decode()}'
    except Exception as e:
        return None, str(e)

print('=' * 60)
print('  Supabase DB Health Check')
print('=' * 60)

for table in TABLES:
    # row count
    rows, err = sb_get(table, 'select=id&limit=200')
    if err:
        print(f'\n  [{table}]  ERROR: {err}')
        continue
    print(f'\n  [{table}]  {len(rows)} row(s) found')
    if not rows:
        continue

    # latest 3 rows with all fields
    rows, err = sb_get(table, 'order=ts.desc&limit=3')
    if err:
        print(f'    latest fetch error: {err}')
        continue
    for r in rows:
        print(f'    {r}')

# per-student summary from stress_scores
print('\n' + '=' * 60)
print('  Per-student latest stress_scores')
print('=' * 60)
for sid in ['S01', 'S02', 'S03', 'S04', 'S05']:
    rows, err = sb_get('stress_scores', f'student_id=eq.{sid}&order=ts.desc&limit=1')
    if err or not rows:
        print(f'  {sid}: no data')
        continue
    r = rows[0]
    print(f"  {sid}  st={r.get('st','?'):.3f}  "
          f"sphys={r.get('sphys','?'):.3f}  "
          f"svis={r.get('svis','?'):.3f}  "
          f"e={r.get('e','?'):.3f}  "
          f"alert={r.get('alert','?')}  "
          f"ts={str(r.get('ts','?'))[:19]}")
