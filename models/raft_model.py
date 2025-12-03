def ensure_rafts_for_date_slot(db, date, slot, rafts_per_slot, capacity):
    existing = list(db.rafts.find({'day': date, 'slot': slot}).sort('raft_id', 1))
    if len(existing) >= rafts_per_slot:
        return
    existing_ids = {r['raft_id'] for r in existing}
    to_create = []
    for rid in range(1, rafts_per_slot + 1):
        if rid not in existing_ids:
            to_create.append({'day': date, 'slot': slot, 'raft_id': rid, 'occupancy': 0, 'is_special': False, 'capacity': capacity})
    if to_create:
        db.rafts.insert_many(to_create)
