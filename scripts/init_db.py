from pymongo import MongoClient
from werkzeug.security import generate_password_hash
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from config import MONGO_URI as mongo_uri
client = MongoClient(mongo_uri)
db = client.get_database("raft_booking")
admin_email = "admin123@gmail.com"
admin_password = "admin123"
hashed_pw = generate_password_hash(admin_password)
existing = db.users.find_one({"email": admin_email})
if existing:
    db.users.update_one({"email": admin_email}, {"$set": {"password_hash": hashed_pw, "role":"admin", "name":"Admin", "phone":"0000000000"}})
    print(f"✅ Admin user updated (email: {admin_email})")
else:
    db.users.insert_one({"name":"Admin", "email": admin_email, "phone":"0000000000", "role":"admin", "password_hash": hashed_pw})
    print(f"✅ Admin user created (email: {admin_email}, password: {admin_password})")
settings = {
    "_id":"system_settings",
    "days":30,
    "slots":4,
    "rafts_per_slot":5,
    "capacity":6,
    "time_slots":["7:00–9:00","10:00–12:00","13:00–15:00","15:30–17:30"]
}
db.settings.replace_one({"_id":"system_settings"}, settings, upsert=True)
print("✅ Default system settings inserted/updated.")
