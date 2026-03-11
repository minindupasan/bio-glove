import firebase_admin
from firebase_admin import credentials, db

cred = credentials.Certificate("ml/serviceAccountKey.json")
firebase_admin.initialize_app(cred, {"databaseURL": "https://smart-classroom-981e2-default-rtdb.asia-southeast1.firebasedatabase.app"})

print(db.reference("environment").get())
