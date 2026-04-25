"""
Fetches and pretty-prints the entire Firebase RTDB.
Run: python fetch_db.py
"""
import json, urllib.request

DB   = "https://smart-classroom-981e2-default-rtdb.asia-southeast1.firebasedatabase.app"
AUTH = "mYcjkCxN949mjqC8qbJLeZdO8Y3Iby6DwLTCeLXD"

req  = urllib.request.Request(f"{DB}/.json?auth={AUTH}")
data = json.loads(urllib.request.urlopen(req, timeout=5).read())
print(json.dumps(data, indent=2))
