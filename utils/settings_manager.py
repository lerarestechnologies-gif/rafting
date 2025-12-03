# utils/settings_manager.py
"""
Settings management utilities for handling settings updates and cache invalidation.
"""
from datetime import timedelta
from utils.allocation_logic import load_settings
from models.raft_model import ensure_rafts_for_date_slot

def invalidate_settings_cache(app):
    """Invalidate the settings cache in Flask app config."""
    if 'SETTINGS_CACHE' in app.config:
        del app.config['SETTINGS_CACHE']

def refresh_settings_cache(app, db):
    """Refresh the settings cache with latest values from database."""
    settings = load_settings(db)
    app.config['SETTINGS_CACHE'] = settings
    return settings

def get_fresh_settings(app, db):
    """Get fresh settings, bypassing cache."""
    return load_settings(db)

def regenerate_rafts_for_settings_change(db, old_settings, new_settings):
    """
    Regenerate rafts when settings change that affect raft structure.
    Returns dict with info about what was regenerated.
    """
    changes = {
        'rafts_regenerated': False,
        'capacity_updated': False,
        'slots_added': [],
        'slots_removed': []
    }
    
    # Check if rafts_per_slot changed - need to add/remove rafts
    old_rafts_per_slot = old_settings.get('rafts_per_slot', 5)
    new_rafts_per_slot = new_settings.get('rafts_per_slot', 5)
    capacity = new_settings.get('capacity', 6)
    
    # Check if capacity changed - update existing rafts
    old_capacity = old_settings.get('capacity', 6)
    new_capacity = new_settings.get('capacity', 6)
    
    if old_capacity != new_capacity:
        # Update capacity field for all existing rafts
        db.rafts.update_many({}, {'$set': {'capacity': new_capacity}})
        changes['capacity_updated'] = True
    
    # Check if time slots changed
    old_slots = set(old_settings.get('time_slots', []))
    new_slots = set(new_settings.get('time_slots', []))
    
    removed_slots = old_slots - new_slots
    added_slots = new_slots - old_slots
    
    if removed_slots:
        changes['slots_removed'] = list(removed_slots)
        # Optionally: remove rafts for removed slots (or keep them for historical data)
        # For now, we'll keep them but they won't be used in new bookings
    
    if added_slots:
        changes['slots_added'] = list(added_slots)
    
    # Regenerate rafts for all existing dates if rafts_per_slot changed or slots added
    if old_rafts_per_slot != new_rafts_per_slot or added_slots:
        changes['rafts_regenerated'] = True
        
        # Get all unique dates from existing rafts and bookings
        from datetime import date as date_type
        today = date_type.today()
        days = new_settings.get('days', 30)
        
        # Generate dates for next N days
        dates = [(today + timedelta(days=i)).isoformat() for i in range(days)]
        
        # Also get dates from existing bookings
        existing_dates = set()
        for booking in db.bookings.find({}, {'date': 1}):
            if booking.get('date'):
                existing_dates.add(booking['date'])
        
        # Also get dates from existing rafts
        for raft in db.rafts.find({}, {'day': 1}):
            if raft.get('day'):
                existing_dates.add(raft['day'])
        
        all_dates = set(dates) | existing_dates
        
        # Regenerate rafts for all dates and all slots (old and new)
        all_slots = old_slots | new_slots
        for date_str in all_dates:
            for slot in all_slots:
                if slot in removed_slots:
                    continue  # Skip removed slots
                
                # Ensure rafts exist with new settings
                ensure_rafts_for_date_slot(db, date_str, slot, new_rafts_per_slot, capacity)
                
                # If rafts_per_slot decreased, remove extra rafts
                existing_rafts = list(db.rafts.find({'day': date_str, 'slot': slot}).sort('raft_id', 1))
                if len(existing_rafts) > new_rafts_per_slot:
                    # Remove rafts beyond the new limit (only if they have no occupancy)
                    # If they have occupancy, we need to handle it - for now, we'll keep them but they won't be returned
                    for raft in existing_rafts[new_rafts_per_slot:]:
                        occupancy = raft.get('occupancy', 0)
                        if occupancy == 0:
                            # Safe to delete empty rafts
                            db.rafts.delete_one({'_id': raft['_id']})
                        # Note: If raft has occupancy > 0, we keep it in DB but won't display it
                        # The occupancy_detail endpoint will limit results to new_rafts_per_slot
    
    return changes

