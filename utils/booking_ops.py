from bson.objectid import ObjectId
from utils.allocation_logic import allocate_raft, load_settings, get_allocation_pattern
from models.raft_model import ensure_rafts_for_date_slot
from datetime import datetime, date

def get_deallocation_amounts(db, date, slot, group_size, raft_ids):
    """
    Determine how many people to remove from each raft following the same
    allocation pattern logic used during booking.
    Returns a list of tuples: [(raft_id, amount_to_remove), ...]
    """
    settings = load_settings(db)
    capacity = settings['capacity']
    rafts_per_slot = settings.get('rafts_per_slot', 5)
    max_people_per_slot = settings.get('rafts_per_slot', 5) * (capacity + 1)
    
    # Fetch current raft states
    raft_map = {}
    for rid in raft_ids:
        raft = db.rafts.find_one({'day': date, 'slot': slot, 'raft_id': int(rid)})
        if raft:
            raft_map[int(rid)] = raft
    
    if not raft_map:
        return []
    
    # ---------- Bulk booking deallocation ----------
    # Mirror the bulk booking logic: if group_size > (rafts_per_slot * capacity),
    # reverse the equal distribution used during allocation
    if group_size > (rafts_per_slot * capacity):
        base = group_size // rafts_per_slot
        rem = group_size % rafts_per_slot
        deallocations = []
        # Sort raft_ids to match the deterministic ordering used in allocation
        sorted_raft_ids = sorted(raft_ids, key=lambda x: int(x))
        for idx, rid in enumerate(sorted_raft_ids):
            amount = base + (1 if idx < rem else 0)
            deallocations.append((int(rid), amount))
        return deallocations
    
    # ---------- Regular booking deallocation using allocation pattern ----------
    # Get the allocation pattern that was used during booking
    allocation = get_allocation_pattern(group_size, max_people_per_slot)
    
    if not allocation:
        # Fallback: if pattern generation fails, use simple division
        per = group_size // len(raft_ids)
        rem = group_size % len(raft_ids)
        deallocations = []
        for idx, rid in enumerate(raft_ids):
            amount = per + (1 if idx < rem else 0)
            deallocations.append((int(rid), amount))
        return deallocations
    
    # Match allocation pattern parts to rafts
    # The allocation logic allocates parts sequentially - either merged into existing rafts
    # or placed into empty rafts. We need to match pattern parts to rafts deterministically.
    
    sorted_raft_ids = sorted(raft_ids, key=lambda x: int(x))
    deallocations = []
    
    # Strategy: Match allocation pattern parts to rafts in order
    # This mirrors the allocation logic which processes parts sequentially
    
    # For single-raft allocations (4-7 people), all people go to one raft
    if len(allocation) == 1:
        # Single pattern part goes to the first raft
        if sorted_raft_ids:
            deallocations.append((int(sorted_raft_ids[0]), allocation[0]))
            # Verify total matches group_size
            if allocation[0] != group_size and len(sorted_raft_ids) > 1:
                # Edge case: if group_size doesn't match pattern, distribute remainder
                remaining = group_size - allocation[0]
                if remaining > 0:
                    per_remaining = remaining // (len(sorted_raft_ids) - 1)
                    rem_remaining = remaining % (len(sorted_raft_ids) - 1)
                    for idx, rid in enumerate(sorted_raft_ids[1:], 1):
                        amount = per_remaining + (1 if idx <= rem_remaining else 0)
                        if amount > 0:
                            deallocations.append((int(rid), amount))
    
    # For multi-raft allocations (8+ people), match each pattern part to a raft
    elif len(allocation) >= 2:
        # Allocate pattern parts sequentially to rafts (matching allocation order)
        remaining_rafts = sorted_raft_ids.copy()
        
        for pattern_part in allocation:
            if not remaining_rafts:
                break
            # Assign this pattern part to the next available raft
            rid = remaining_rafts.pop(0)
            deallocations.append((int(rid), pattern_part))
        
        # Check if all people are accounted for
        allocated_so_far = sum(amount for _, amount in deallocations)
        remaining_people = group_size - allocated_so_far
        
        # If there are remaining rafts or remaining people, handle it
        if remaining_people > 0:
            if remaining_rafts:
                # Distribute remaining people to remaining rafts
                per = remaining_people // len(remaining_rafts)
                rem = remaining_people % len(remaining_rafts)
                for idx, rid in enumerate(remaining_rafts):
                    amount = per + (1 if idx < rem else 0)
                    if amount > 0:
                        deallocations.append((int(rid), amount))
            else:
                # No more rafts but still have remaining people - shouldn't happen
                # Add to the last raft as safety
                if deallocations:
                    last_raft_id, last_amount = deallocations[-1]
                    deallocations[-1] = (last_raft_id, last_amount + remaining_people)
    
    else:
        # Fallback: simple division (shouldn't reach here if get_allocation_pattern works)
        per = group_size // len(sorted_raft_ids)
        rem = group_size % len(sorted_raft_ids)
        for idx, rid in enumerate(sorted_raft_ids):
            amount = per + (1 if idx < rem else 0)
            deallocations.append((int(rid), amount))
    
    # Validate: ensure total matches group_size
    total_deallocated = sum(amount for _, amount in deallocations)
    if total_deallocated != group_size:
        # If mismatch, adjust the last entry to match
        if deallocations:
            diff = group_size - total_deallocated
            last_raft_id, last_amount = deallocations[-1]
            deallocations[-1] = (last_raft_id, last_amount + diff)
    
    return deallocations

def cancel_booking(db, booking_oid):
    """
    Cancel a booking following the same allocation pattern logic used during booking.
    Uses get_allocation_pattern as the source of truth for deallocation amounts.
    """
    b = db.bookings.find_one({'_id': booking_oid})
    if not b:
        return {'error': 'Booking not found'}
    if b.get('status') == 'Cancelled':
        return {'message': 'Already cancelled'}
    
    # Only process confirmed bookings with raft allocations
    raft_ids = b.get('raft_allocations', [])
    group_size = int(b.get('group_size', 0))
    booking_date = b.get('date')
    booking_slot = b.get('slot')
    
    if not raft_ids or b.get('status') != 'Confirmed':
        # No raft allocations to free, just mark as cancelled
        db.bookings.update_one({'_id': booking_oid}, {'$set': {'status': 'Cancelled', 'raft_allocations': [], 'cancelled_by_admin': True}})
        return {'message': 'Booking cancelled (no raft allocations to free).'}
    
    # Get deallocation amounts using the same pattern logic as allocation
    settings = load_settings(db)
    capacity = settings['capacity']
    
    deallocations = get_deallocation_amounts(db, booking_date, booking_slot, group_size, raft_ids)
    
    # Apply deallocation to each raft following allocation pattern rules
    for raft_id, amount_to_remove in deallocations:
        raft = db.rafts.find_one({'day': booking_date, 'slot': booking_slot, 'raft_id': raft_id})
        if not raft:
            continue
        
        current_occupancy = max(0, raft.get('occupancy', 0))
        new_occupancy = max(0, current_occupancy - amount_to_remove)
        
        # Build update following allocation pattern rules
        update_data = {'$set': {'occupancy': new_occupancy}}
        
        # Clear is_special flag following allocation logic rules:
        # - If occupancy becomes 0, clear special flag
        # - If occupancy is not 7, clear special flag (7 is the only special case)
        if new_occupancy == 0:
            update_data['$set']['is_special'] = False
        elif new_occupancy != 7:
            update_data['$set']['is_special'] = False
        # Note: If new_occupancy == 7, we keep is_special as True
        # (it may have been special before, or may have other bookings)
        
        db.rafts.update_one(
            {'day': booking_date, 'slot': booking_slot, 'raft_id': raft_id},
            update_data
        )
    
    # Update booking document
    db.bookings.update_one(
        {'_id': booking_oid},
        {'$set': {'status': 'Cancelled', 'raft_allocations': [], 'cancelled_by_admin': True}}
    )
    
    return {'message': 'Booking cancelled and capacity freed using allocation pattern logic.'}

def check_capacity_available(db, date, slot, group_size):
    """
    Check if a date/slot has capacity for a given group_size without allocating.
    Uses the same logic as allocate_raft but only checks, doesn't modify DB.
    Returns True if capacity is available, False otherwise.
    This mirrors the exact capacity checking logic from allocate_raft.
    """
    settings = load_settings(db)
    capacity = settings['capacity']
    rafts_per_slot = settings.get('rafts_per_slot', 5)
    max_people_per_slot = settings.get('rafts_per_slot', 5) * (capacity + 1)
    
    # Ensure rafts exist for this date/slot
    ensure_rafts_for_date_slot(db, date, slot, rafts_per_slot, capacity)
    
    # Fetch rafts for date+slot sorted, limit to configured number
    rafts = list(db.rafts.find({'day': date, 'slot': slot}).sort('raft_id', 1).limit(rafts_per_slot))
    if not rafts:
        return False
    
    # ---------- Bulk booking check (mirrors allocate_raft logic) ----------
    if group_size > (rafts_per_slot * capacity):
        # Check whether all rafts in this slot are empty
        all_empty = all(r.get('occupancy', 0) == 0 for r in rafts)
        if not all_empty:
            return False
        
        # Compute the maximum allowed bulk booking for an empty slot
        max_bulk = rafts_per_slot * (capacity + 1)
        if group_size > max_bulk:
            return False
        return True
    
    # ---------- Regular booking capacity check (mirrors allocate_raft logic) ----------
    # Special case: if group_size is 7, check for empty rafts
    if group_size == 7:
        empty_rafts = [r for r in rafts if r.get('occupancy', 0) == 0]
        if not empty_rafts:
            return False
    else:
        # For other group sizes, check standard capacity (initial check)
        total_vacancy = sum(max(capacity - r.get('occupancy', 0), 0) for r in rafts)
        if total_vacancy < group_size:
            return False
    
    # Get allocation pattern
    allocation = get_allocation_pattern(group_size, max_people_per_slot)
    if not allocation:
        return False
    
    # For small groups (<4), check if we can merge (mirrors allocate_raft logic)
    if group_size < 4:
        for r in rafts:
            if not r.get('is_special', False) and r.get('occupancy', 0) > 0:
                vacancy = capacity - r.get('occupancy', 0)
                if vacancy >= group_size:
                    return True
        return False
    
    # For larger groups, simulate allocation to see if all parts can be placed
    # Create a copy of rafts to simulate allocation without modifying DB
    simulated_rafts = []
    for r in rafts:
        simulated_rafts.append({
            'raft_id': r.get('raft_id'),
            'occupancy': r.get('occupancy', 0),
            'is_special': r.get('is_special', False),
            'capacity': r.get('capacity', capacity)
        })
    
    # Try to place all allocation parts (mirroring allocate_raft logic)
    remaining_parts = allocation.copy()
    
    # First, try to merge parts into existing non-special rafts
    for part in list(remaining_parts):
        for r in simulated_rafts:
            if not r.get('is_special', False) and r.get('occupancy', 0) > 0:
                vacancy = capacity - r['occupancy']
                if vacancy >= part:
                    # Can merge this part
                    r['occupancy'] += part
                    remaining_parts.remove(part)
                    break
    
    # If all parts merged, capacity is available
    if not remaining_parts:
        return True
    
    # Check if we have enough empty rafts for remaining parts
    empty_count = sum(1 for r in simulated_rafts if r.get('occupancy', 0) == 0)
    if len(remaining_parts) > empty_count:
        return False
    
    return True

def postpone_booking(db, booking_oid, new_date, new_slot):
    """
    Postpone a booking to a new date/slot.
    Only proceeds if target slot has available capacity.
    Never creates PENDING state - fails if capacity unavailable.
    """
    b = db.bookings.find_one({'_id': booking_oid})
    if not b:
        return {'error': 'Booking not found'}
    
    # Validate date format and ensure it's in the future
    try:
        new_date_obj = datetime.strptime(new_date, '%Y-%m-%d').date()
        today = date.today()
        if new_date_obj < today:
            return {'error': 'New date must be in the future'}
    except ValueError:
        return {'error': 'Invalid date format. Use YYYY-MM-DD'}
    
    # Validate slot exists in settings
    settings = load_settings(db)
    if new_slot not in settings.get('time_slots', []):
        return {'error': f'Invalid time slot. Valid slots: {", ".join(settings.get("time_slots", []))}'}
    
    # Get booking details
    raft_ids = b.get('raft_allocations', [])
    group_size = int(b.get('group_size', 0))
    old_date = b.get('date')
    old_slot = b.get('slot')
    is_confirmed = b.get('status') == 'Confirmed'
    
    # Check if moving to same date/slot
    if old_date == new_date and old_slot == new_slot:
        return {'error': 'Booking is already scheduled for this date and time slot.'}
    
    # ---------- STEP 1: Check capacity in target slot FIRST ----------
    # Ensure rafts exist for the new date/slot
    ensure_rafts_for_date_slot(db, new_date, new_slot, settings['rafts_per_slot'], settings['capacity'])
    
    # Check if target slot has available capacity
    has_capacity = check_capacity_available(db, new_date, new_slot, group_size)
    
    if not has_capacity:
        # Capacity check failed - do NOT modify anything, return error
        return {'error': 'Postpone failed — timeslot is full.'}
    
    # ---------- STEP 2: Capacity available, proceed with move ----------
    # Store original raft states for rollback if needed
    original_raft_states = {}
    if is_confirmed and raft_ids:
        for rid in raft_ids:
            raft = db.rafts.find_one({'day': old_date, 'slot': old_slot, 'raft_id': int(rid)})
            if raft:
                original_raft_states[int(rid)] = {
                    'occupancy': raft.get('occupancy', 0),
                    'is_special': raft.get('is_special', False)
                }
    
    try:
        # Free old rafts if confirmed using allocation pattern logic
        if is_confirmed and raft_ids:
            deallocations = get_deallocation_amounts(db, old_date, old_slot, group_size, raft_ids)
            
            # Apply deallocation to each raft following allocation pattern rules
            for raft_id, amount_to_remove in deallocations:
                raft = db.rafts.find_one({'day': old_date, 'slot': old_slot, 'raft_id': raft_id})
                if not raft:
                    continue
                
                current_occupancy = max(0, raft.get('occupancy', 0))
                new_occupancy = max(0, current_occupancy - amount_to_remove)
                
                # Build update following allocation pattern rules
                update_data = {'$set': {'occupancy': new_occupancy}}
                
                # Clear is_special flag following allocation logic rules
                if new_occupancy == 0:
                    update_data['$set']['is_special'] = False
                elif new_occupancy != 7:
                    update_data['$set']['is_special'] = False
                
                db.rafts.update_one(
                    {'day': old_date, 'slot': old_slot, 'raft_id': raft_id},
                    update_data
                )
        
        # Allocate in new slot
        res = allocate_raft(db, None, new_date, new_slot, group_size)
        
        # Verify allocation succeeded
        if res.get('status') != 'Confirmed':
            # Allocation failed - rollback old slot changes
            if is_confirmed and raft_ids and original_raft_states:
                for raft_id, original_state in original_raft_states.items():
                    db.rafts.update_one(
                        {'day': old_date, 'slot': old_slot, 'raft_id': raft_id},
                        {
                            '$set': {
                                'occupancy': original_state['occupancy'],
                                'is_special': original_state['is_special']
                            }
                        }
                    )
            return {'error': 'Postpone failed — timeslot is full.'}
        
        # Allocation succeeded - update booking document
        update_data = {
            'date': new_date,
            'slot': new_slot,
            'status': 'Confirmed',  # Always confirmed since we checked capacity
            'raft_allocations': res.get('rafts', []),
            'rescheduled_by_admin': True
        }
        db.bookings.update_one({'_id': booking_oid}, {'$set': update_data})
        
        # Fetch updated booking to return
        updated_booking = db.bookings.find_one({'_id': booking_oid})
        
        return {
            'message': f'Booking rescheduled to {new_date} at {new_slot}',
            'result': res,
            'booking': {
                'id': str(updated_booking['_id']),
                'date': updated_booking.get('date'),
                'slot': updated_booking.get('slot'),
                'status': updated_booking.get('status'),
                'raft_allocations': updated_booking.get('raft_allocations', [])
            }
        }
    
    except Exception as e:
        # Rollback on any error
        if is_confirmed and raft_ids and original_raft_states:
            for raft_id, original_state in original_raft_states.items():
                db.rafts.update_one(
                    {'day': old_date, 'slot': old_slot, 'raft_id': raft_id},
                    {
                        '$set': {
                            'occupancy': original_state['occupancy'],
                            'is_special': original_state['is_special']
                        }
                    }
                )
        return {'error': f'Postpone failed: {str(e)}'}
