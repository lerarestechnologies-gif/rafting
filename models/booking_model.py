from datetime import datetime, timezone
from bson.objectid import ObjectId

def create_booking(db, name, email, phone, date, slot, group_size, status='Pending', raft_allocations=None, amount_per_person=0, total_amount=0):
    if raft_allocations is None:
        raft_allocations = []
    now_utc = datetime.now(timezone.utc)

    booking = {
        'user_name': name,
        'email': email,
        'phone': phone,
        'date': date,
        'slot': slot,
        'group_size': int(group_size),
        'raft_allocations': raft_allocations,
        'status': status,
        'amount_per_person': float(amount_per_person),
        'total_amount': float(total_amount),

        # ✅ TIMESTAMPS (ADMIN NEEDS THIS)
        'created_at': now_utc,
        'updated_at': now_utc
    }
    res = db.bookings.insert_one(booking)
    return str(res.inserted_id)

def find_latest_by_contact(db, email, phone):
    return db.bookings.find({'email': email, 'phone': phone}).sort('created_at', -1).limit(1)

def update_booking_status(db, booking_id, status, raft_allocations=None):
    from bson.objectid import ObjectId
    update = {'status': status}
    if raft_allocations is not None:
        update['raft_allocations'] = raft_allocations
    db.bookings.update_one({'_id': ObjectId(booking_id)}, {'$set': update})
