# utils/allocation_logic.py
import math
from datetime import datetime, date, timedelta

def load_settings(db):
    settings = db.settings.find_one({'_id': 'system_settings'})
    if not settings:
        # Default settings with date range
        today = date.today()
        default_end = date(today.year, today.month, today.day) + timedelta(days=29)  # 30 days total
        settings = {
            'rafts_per_slot': 5,
            'capacity': 6,
            'time_slots': [], # No hardcoded defaults; must be set by admin
            'start_date': today.isoformat(),
            'end_date': default_end.isoformat(),
            'days': 30
        }
    else:
        # If we have start_date and end_date, ensure days is calculated correctly
        if 'start_date' in settings and 'end_date' in settings:
            try:
                start = datetime.strptime(settings['start_date'], '%Y-%m-%d').date()
                end = datetime.strptime(settings['end_date'], '%Y-%m-%d').date()
                calculated_days = (end - start).days + 1
                # Update days if it doesn't match calculated value
                if settings.get('days') != calculated_days:
                    settings['days'] = calculated_days
            except (ValueError, TypeError):
                # If date parsing fails, keep existing days value
                pass
        # Backward compatibility: if no dates but has days, calculate dates from today
        elif 'days' in settings and 'start_date' not in settings:
            today = date.today()
            settings['start_date'] = today.isoformat()
            end_date = today + timedelta(days=settings['days'] - 1)
            settings['end_date'] = end_date.isoformat()
    
    # Calculate max_people_per_slot dynamically from rafts_per_slot * (capacity + 1)
    # The +1 accounts for special 7-person rafts when capacity is 6
    # Always calculate it fresh, ignoring any old value that might exist in the database
    settings['max_people_per_slot'] = settings.get('rafts_per_slot', 5) * (settings.get('capacity', 6) + 1)
    return settings

# ---------- Allocation Pattern ----------
def get_allocation_pattern(people, max_per_slot):
    allocation = []
    if 4 <= people <= 7:
        allocation = [people]
    elif people == 8:
        allocation = [6, 2]
    elif people == 9:
        allocation = [6, 3]
    elif people == 10:
        allocation = [6, 4]
    elif 11 <= people <= max_per_slot:
        rafts_needed = math.ceil(people / 7.0)
        surplus = people - 6 * rafts_needed
        if surplus >= 0:
            allocation = [6 if i < rafts_needed - surplus else 7 for i in range(rafts_needed)]
        else:
            allocation = [6] * (rafts_needed - 1) + [6 + surplus]
    return allocation

def try_merge_into_existing_rafts(db, rafts, people, capacity):
    """Try to merge `people` into any partially filled, non-special raft.
    Returns raft_id if merged (and updates DB), else None.
    """
    for r in rafts:
        if not r.get('is_special', False) and r.get('occupancy', 0) > 0:
            vacancy = capacity - r['occupancy']
            if vacancy >= people:
                db.rafts.update_one({'_id': r['_id']}, {'$inc': {'occupancy': people}})
                return r['raft_id']
    return None

def allocate_raft(db, user_id, date, slot, group_size):
    """Implements C-style allocation:
    - small groups (<4) try merging
    - 4-7 single raft
    - 8..10 specific splits
    - >10 split into 6/7 patterns
    Returns {'status','rafts','message'}
    """
    settings = load_settings(db)
    capacity = settings['capacity']
    # Calculate max_people_per_slot dynamically: rafts_per_slot * (capacity + 1)
    # The +1 accounts for special 7-person rafts when capacity is 6
    max_people_per_slot = settings.get('rafts_per_slot', 5) * (capacity + 1)

    # Ensure rafts exist for this date/slot
    from models.raft_model import ensure_rafts_for_date_slot
    ensure_rafts_for_date_slot(db, date, slot, settings.get('rafts_per_slot', 5), capacity)
    
    # fetch rafts for date+slot sorted, limit to configured number
    rafts_per_slot = settings.get('rafts_per_slot', 5)
    rafts = list(db.rafts.find({'day': date, 'slot': slot}).sort('raft_id', 1).limit(rafts_per_slot))
    if not rafts:
        return {'status': 'Pending', 'rafts': [], 'message': 'No rafts initialized for this slot.'}

    # ---------- Bulk booking logic ----------
    # A bulk booking is when group_size > (rafts_per_slot * capacity)
    # Allowed only if the slot is completely empty. In that case we allow
    # up to rafts_per_slot * (capacity + 1) people (special 7-person mode for each raft).
    if group_size > (rafts_per_slot * capacity):
        # Check whether all rafts in this slot are empty
        all_empty = all(r.get('occupancy', 0) == 0 for r in rafts)
        if not all_empty:
            return {'status': 'Pending', 'rafts': [], 'message': 'Pending – Bulk booking can only be done in an empty slot.'}

        # Compute the maximum allowed bulk booking for an empty slot
        max_bulk = rafts_per_slot * (capacity + 1)
        if group_size > max_bulk:
            return {'status': 'Pending', 'rafts': [], 'message': 'Not enough capacity in this slot.'}

        # Distribute people equally among all rafts, some rafts may get +1 to account for remainder
        base = group_size // rafts_per_slot
        rem = group_size % rafts_per_slot
        placed = []
        # Ensure deterministic ordering (already sorted above)
        for idx, r in enumerate(rafts):
            occ = base + (1 if idx < rem else 0)
            # Mark all rafts as special (7-capacity mode) and set occupancy
            db.rafts.update_one({'_id': r['_id']}, {'$set': {'occupancy': occ, 'is_special': True}})
            placed.append(r['raft_id'])

        return {'status': 'Confirmed', 'rafts': placed, 'message': f'Bulk allocated to rafts: {placed}'}


    # Calculate total vacancy with special raft consideration
    # - Empty raft: vacancy = capacity + 1 (can be a special 7-person raft)
    # - Non-empty, non-special raft: vacancy = max(capacity - occupancy, 0)
    # - Special raft: vacancy = 0 (already filled to 7)
    total_vacancy = 0
    for r in rafts:
        occupancy = r.get('occupancy', 0)
        is_special = r.get('is_special', False)
        
        if occupancy == 0:
            # Empty raft can hold capacity + 1 (special 7-person mode)
            total_vacancy += capacity + 1
        elif is_special:
            # Special raft is fully occupied at 7
            total_vacancy += 0
        else:
            # Normal raft: remaining capacity
            total_vacancy += max(capacity - occupancy, 0)
    
    if total_vacancy < group_size:
        return {'status': 'Pending', 'rafts': [], 'message': 'Not enough capacity in this slot.'}

    # small groups (<4) - merge into any partially filled raft
    if group_size < 4:
        merged_raft = try_merge_into_existing_rafts(db, rafts, group_size, capacity)
        if merged_raft:
            return {'status': 'Confirmed', 'rafts': [merged_raft], 'message': f'Merged into Raft {merged_raft}'}
        return {'status': 'Pending', 'rafts': [], 'message': 'No suitable raft to merge small group.'}

    # compute allocation pattern as per C program
    allocation = get_allocation_pattern(group_size, max_people_per_slot)
    if not allocation:
        return {'status': 'Pending', 'rafts': [], 'message': 'Invalid group size or allocation pattern.'}

    # Try merging each allocation piece into existing rafts first
    placed = []
    for i, part in enumerate(list(allocation)):
        if part <= 0:
            continue
        merged = try_merge_into_existing_rafts(db, rafts, part, capacity)
        if merged:
            placed.append(merged)
            allocation[i] = 0  # mark placed

    # Collect unplaced parts
    unplaced = [p for p in allocation if p > 0]
    if not unplaced:
        return {'status': 'Confirmed', 'rafts': placed, 'message': f'All merged ({placed})'}

    # Find empty rafts
    empty_rafts = [r for r in rafts if r.get('occupancy', 0) == 0]
    if len(empty_rafts) < len(unplaced):
        return {'status': 'Pending', 'rafts': [], 'message': 'Not enough empty rafts available.'}

    # Allocate unplaced parts to empty rafts (mark is_special for 7)
    for idx, part in enumerate(unplaced):
        target = empty_rafts[idx]
        db.rafts.update_one({'_id': target['_id']}, {'$set': {'occupancy': part, 'is_special': (part == 7)}})
        placed.append(target['raft_id'])

    return {'status': 'Confirmed', 'rafts': placed, 'message': f'Allocated to rafts: {placed}'}
