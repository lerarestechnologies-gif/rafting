from pymongo import MongoClient
import sys
import os

# Use project config to get the MongoDB URI (avoid falling back to localhost on servers)
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from config import MONGO_URI
client = MongoClient(MONGO_URI)
db = client.get_database("raft_booking")

settings = db.settings.find_one({'_id':'system_settings'}) or {'rafts_per_slot':5,'capacity':6}
capacity = settings.get('capacity', 6)
rafts_per_slot = settings.get('rafts_per_slot', 5)

print("Resetting all raft occupancy to 0...")
db.rafts.update_many({}, {'$set': {'occupancy': 0, 'is_special': False}})

# Ensure rafts exist for all booking dates & slots
bookings = list(db.bookings.find({}))
dates_slots = set()
for b in bookings:
    dates_slots.add((b.get('date'), b.get('slot')))

def ensure_rafts_for_date_slot(db, date, slot, rafts_per_slot, capacity):
    existing = list(db.rafts.find({'day': date, 'slot': slot}).sort('raft_id', 1))
    existing_ids = {r['raft_id'] for r in existing}
    to_create = []
    for rid in range(1, rafts_per_slot + 1):
        if rid not in existing_ids:
            to_create.append({'day': date, 'slot': slot, 'raft_id': rid, 'occupancy': 0, 'is_special': False})
    if to_create:
        db.rafts.insert_many(to_create)

for d, s in dates_slots:
    ensure_rafts_for_date_slot(db, d, s, rafts_per_slot, capacity)

print("Applying confirmed bookings occupancy and verifying raft_allocations...")
from utils.allocation_logic import allocate_raft
for b in db.bookings.find({'status':'Confirmed'}):
    rafts = b.get('raft_allocations', [])
    if rafts:
        group = int(b.get('group_size', 0))
        per = group // len(rafts)
        rem = group % len(rafts)
        for idx, rid in enumerate(rafts):
            add = per + (1 if idx < rem else 0)
            db.rafts.update_one({'day': b['date'], 'slot': b['slot'], 'raft_id': rid}, {'$inc': {'occupancy': add}})
    else:
        res = allocate_raft(db, None, b['date'], b['slot'], int(b.get('group_size', 0)))
        if res.get('status') == 'Confirmed':
            db.bookings.update_one({'_id': b['_id']}, {'$set': {'raft_allocations': res.get('rafts', [])}})
        else:
            print(f"Confirmed booking {b['_id']} could not be re-allocated automatically.")

print("Recompute complete.")
