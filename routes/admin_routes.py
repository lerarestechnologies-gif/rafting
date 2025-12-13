from bson.objectid import ObjectId
from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app, jsonify
from flask_login import login_required, current_user
from utils.allocation_logic import load_settings
from utils.booking_ops import cancel_booking, postpone_booking
from utils.settings_manager import invalidate_settings_cache, refresh_settings_cache, regenerate_rafts_for_settings_change
from models.booking_model import update_booking_status
import datetime
import re
admin_bp = Blueprint('admin', __name__, url_prefix='/admin')

def admin_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role != 'admin':
            flash('Admin only', 'error')
            return redirect(url_for('auth.login'))
        return f(*args, **kwargs)
    return decorated

def subadmin_or_admin_required(f):
    """Allow both admin and subadmin to access the route."""
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin_or_subadmin():
            flash('Access denied', 'error')
            return redirect(url_for('auth.login'))
        return f(*args, **kwargs)
    return decorated

@admin_bp.route('/dashboard')
@login_required
@subadmin_or_admin_required
def dashboard():
    db = current_app.mongo.db
    settings = load_settings(db)
    
    # Build query filter
    query_filter = {}
    today = datetime.date.today()
    tomorrow = today + datetime.timedelta(days=1)
    
    # Sub-Admin: Only show Confirmed bookings for today and tomorrow
    if current_user.is_subadmin():
        query_filter['status'] = 'Confirmed'
        # Automatically restrict to today and tomorrow only
        query_filter['date'] = {
            '$in': [today.isoformat(), tomorrow.isoformat()]
        }
        # Ignore any filter parameters from URL for subadmin
        filter_type = ''
        filter_date = ''
    else:
        # Admin: Use From/To date range parameters (inclusive)
        filter_from = request.args.get('from_date', '')
        filter_to = request.args.get('to_date', '')

        # Also accept multi-value status and slot filters
        filter_status = request.args.getlist('status') or []
        filter_slot = request.args.getlist('slot') or []

        # Validate and apply range filters
        if filter_from and filter_to:
            try:
                # Ensure valid ISO date strings
                _ = datetime.date.fromisoformat(filter_from)
                _ = datetime.date.fromisoformat(filter_to)
                # If from > to, swap to be safe
                if filter_from > filter_to:
                    filter_from, filter_to = filter_to, filter_from
                query_filter['date'] = {'$gte': filter_from, '$lte': filter_to}
            except Exception:
                # Ignore invalid inputs and do not filter
                pass
        elif filter_from:
            # Single start date: treat as exact date
            try:
                _ = datetime.date.fromisoformat(filter_from)
                query_filter['date'] = filter_from
            except Exception:
                pass
        elif filter_to:
            try:
                _ = datetime.date.fromisoformat(filter_to)
                query_filter['date'] = filter_to
            except Exception:
                pass
        # Apply status filter if provided
        try:
            if filter_status:
                # Normalize values (strings)
                statuses = [s for s in filter_status if s]
                if len(statuses) == 1:
                    query_filter['status'] = statuses[0]
                elif len(statuses) > 1:
                    query_filter['status'] = {'$in': statuses}
        except Exception:
            pass

        # Apply slot filter if provided
        try:
            if filter_slot:
                slots = [s for s in filter_slot if s]
                if len(slots) == 1:
                    query_filter['slot'] = slots[0]
                elif len(slots) > 1:
                    query_filter['slot'] = {'$in': slots}
        except Exception:
            pass
    
    # Fetch bookings with filters
    bookings = list(db.bookings.find(query_filter).sort('created_at', -1).limit(500))
    

    def format_phone_for_admin(phone):
        if not phone:
            return ""

        # Extract digits only
        digits = re.sub(r"\D", "", phone)

        # Case 1: exactly 10 digits → assume Indian number
        if len(digits) == 10:
            return f"+91 {digits}"

        # Case 2: starts with 91 and has 12 digits
        if len(digits) == 12 and digits.startswith("91"):
            return f"+91 {digits[2:]}"

        # Case 3: already stored as +91XXXXXXXXXX
        if phone.startswith("+91"):
            return phone.replace("+91", "+91 ")

        # Fallback: return original (just in case)
        return phone


    for booking in bookings:
        booking["phone_display"] = format_phone_for_admin(
            booking.get("phone", "")
        )

    # For subadmin, don't pass filter parameters
    if current_user.is_subadmin():
        return render_template('admin_dashboard.html', 
                             bookings=bookings, 
                             settings=settings,
                             selected_from='',
                             selected_to='',
                             selected_status=[],
                             selected_slot=[])
    else:
        return render_template('admin_dashboard.html', 
                             bookings=bookings, 
                             settings=settings,
                             selected_from=filter_from or '',
                             selected_to=filter_to or '',
                             selected_status=filter_status or [],
                             selected_slot=filter_slot or [])

@admin_bp.route('/calendar')
@login_required
@admin_required  # Only admin, not subadmin
def calendar():
    db = current_app.mongo.db
    settings = load_settings(db)
    
    # Use start_date and end_date from settings if available
    start_date_str = settings.get('start_date')
    end_date_str = settings.get('end_date')
    
    if start_date_str and end_date_str:
        try:
            # Use datetime.datetime.strptime when module 'datetime' is imported
            start_date = datetime.datetime.strptime(start_date_str, '%Y-%m-%d').date()
            end_date = datetime.datetime.strptime(end_date_str, '%Y-%m-%d').date()
            
            # Generate all dates in the range
            dates = []
            current = start_date
            while current <= end_date:
                dates.append(current.isoformat())
                current += datetime.timedelta(days=1)
        except (ValueError, TypeError):
            # Fallback to old calculation if date parsing fails
            days = settings.get('days', 30)
            today = datetime.date.today()
            dates = [(today + datetime.timedelta(days=i)).isoformat() for i in range(days)]
    else:
        # Backward compatibility: calculate from days
        days = settings.get('days', 30)
        today = datetime.date.today()
        dates = [(today + datetime.timedelta(days=i)).isoformat() for i in range(days)]
    
    calendar = {}
    for d in dates:
        entries = list(db.bookings.find({'date': d}).sort('created_at', -1))
        calendar[d] = entries
    return render_template('admin_calendar.html', calendar=calendar, settings=settings)

@admin_bp.route('/bookings/<booking_id>/status', methods=['POST'])
@login_required
@admin_required  # Only admin, not subadmin
def change_status(booking_id):
    db = current_app.mongo.db
    status = request.form.get('status')
    raft_ids_raw = request.form.get('raft_ids','')
    raft_ids = []
    if raft_ids_raw:
        try:
            raft_ids = [int(x.strip()) for x in raft_ids_raw.split(',') if x.strip()]
        except:
            raft_ids = []
    update_booking_status(db, booking_id, status, raft_allocations=raft_ids if raft_ids else None)
    flash('Booking updated', 'success')
    return redirect(url_for('admin.dashboard'))

@admin_bp.route('/settings', methods=['GET','POST'])
@login_required
@admin_required  # Only admin, not subadmin
def settings_page():
    db = current_app.mongo.db
    if request.method == 'POST':
        # Get old settings before updating
        old_settings = load_settings(db)
        
        # Parse and validate new settings
        try:
            from datetime import datetime, date
            
            # Get start_date and end_date from form
            start_date_str = request.form.get('start_date')
            end_date_str = request.form.get('end_date')
            days_from_form = request.form.get('days')  # Calculated by frontend
            
            # Validate date inputs
            if not start_date_str or not end_date_str:
                flash('Both start date and end date are required', 'error')
                return render_template('settings.html', settings=old_settings)
            
            # Parse dates
            try:
                start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
                end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
            except ValueError:
                flash('Invalid date format. Please use YYYY-MM-DD format.', 'error')
                return render_template('settings.html', settings=old_settings)
            
            # Validate date range
            if end_date < start_date:
                flash('End date must be greater than or equal to start date.', 'error')
                return render_template('settings.html', settings=old_settings)
            
            # Calculate days: end_date - start_date + 1
            days = (end_date - start_date).days + 1
            
            # Validate calculated days matches frontend calculation (safety check)
            if days_from_form and int(days_from_form) != days:
                flash('Date range calculation mismatch. Please try again.', 'error')
                return render_template('settings.html', settings=old_settings)
            
            if days < 1:
                flash('Date range must be at least 1 day.', 'error')
                return render_template('settings.html', settings=old_settings)
            
            data = {
                '_id': 'system_settings',
                'start_date': start_date_str,
                'end_date': end_date_str,
                'days': days,
                'slots': int(request.form.get('slots')) if request.form.get('slots') else len(request.form.get('time_slots', '').split(',')),
                'rafts_per_slot': int(request.form.get('rafts_per_slot')),
                'capacity': int(request.form.get('capacity')),
                'time_slots': [s.strip() for s in request.form.get('time_slots').split(',') if s.strip()]
            }
            
            # Validate settings
            if data['rafts_per_slot'] < 1:
                flash('Rafts per slot must be at least 1', 'error')
                return render_template('settings.html', settings=old_settings)
            if data['capacity'] < 1:
                flash('Capacity per raft must be at least 1', 'error')
                return render_template('settings.html', settings=old_settings)
            if not data['time_slots']:
                flash('At least one time slot is required', 'error')
                return render_template('settings.html', settings=old_settings)
            
            # Parse amount settings (optional)
            weekday_amount_str = request.form.get('weekday_amount', '').strip()
            saturday_amount_str = request.form.get('saturday_amount', '').strip()
            
            # Add amount settings to data if provided
            if weekday_amount_str:
                try:
                    weekday_amount = float(weekday_amount_str)
                    if weekday_amount < 0:
                        flash('Monday–Friday amount must be non-negative', 'error')
                        return render_template('settings.html', settings=old_settings)
                    data['weekday_amount'] = weekday_amount
                except ValueError:
                    flash('Monday–Friday amount must be a valid number', 'error')
                    return render_template('settings.html', settings=old_settings)
            
            if saturday_amount_str:
                try:
                    saturday_amount = float(saturday_amount_str)
                    if saturday_amount < 0:
                        flash('Saturday amount must be non-negative', 'error')
                        return render_template('settings.html', settings=old_settings)
                    data['saturday_amount'] = saturday_amount
                except ValueError:
                    flash('Saturday amount must be a valid number', 'error')
                    return render_template('settings.html', settings=old_settings)
            
        except (ValueError, TypeError) as e:
            flash(f'Invalid input: {str(e)}', 'error')
            settings = load_settings(db)
            return render_template('settings.html', settings=settings)
        
        # Save new settings to database
        db.settings.replace_one({'_id':'system_settings'}, data, upsert=True)
        
        # Invalidate cache and refresh with new settings
        invalidate_settings_cache(current_app)
        refresh_settings_cache(current_app, db)
        
        # Regenerate rafts if needed
        changes = regenerate_rafts_for_settings_change(db, old_settings, data)
        
        # Build success message
        messages = ['✅ Settings updated successfully!']
        if changes['rafts_regenerated']:
            messages.append('🔄 Rafts regenerated for all dates.')
        if changes['capacity_updated']:
            messages.append('📊 Raft capacity updated.')
        if changes['slots_added']:
            messages.append(f'➕ Added time slots: {", ".join(changes["slots_added"])}')
        if changes['slots_removed']:
            messages.append(f'➖ Removed time slots: {", ".join(changes["slots_removed"])} (historical data preserved)')
        
        flash(' | '.join(messages), 'success')
        return render_template('settings.html', settings=data, message=' | '.join(messages))
    
    # GET request - load current settings
    settings = load_settings(db)
    return render_template('settings.html', settings=settings)
# Occupancy endpoints (filter by day param)
from datetime import date as _date

@admin_bp.route('/api/settings', methods=['GET'])
@login_required
@admin_required
def api_get_settings():
    """API endpoint to get fresh settings for frontend refresh."""
    db = current_app.mongo.db
    settings = load_settings(db)
    # Refresh cache
    refresh_settings_cache(current_app, db)
    return jsonify(settings)

@admin_bp.route('/delete_bookings_by_date', methods=['DELETE'])
@login_required
@admin_required  # Only admin, not subadmin
def delete_bookings_by_date():
    """Delete all bookings for a specific date and free up raft occupancy."""
    db = current_app.mongo.db
    date = request.args.get('date')
    
    if not date:
        return jsonify({'error': 'Date parameter is required'}), 400
    
    try:
        # Validate date format
        from datetime import datetime
        datetime.strptime(date, '%Y-%m-%d')
    except ValueError:
        return jsonify({'error': 'Invalid date format. Use YYYY-MM-DD'}), 400
    
    # New: Delegate to safer, auditable delete endpoint via execute (keep old behavior for backwards compat)
    # For backward compatibility still allow this, but perform an archival + delete with audit
    from bson.objectid import ObjectId
    from datetime import datetime as _dt

    # Fetch bookings for the date (exclude already archived/deleted)
    bookings_cursor = db.bookings.find({'date': date, 'deleted': {'$ne': True}})
    bookings = list(bookings_cursor)

    if not bookings:
        return jsonify({'message': f'No bookings found for {date}'}), 200

    # We'll archive each booking into `deletion_archive` and record an audit entry in `deletion_audits`.
    deleted_ids = []
    archived_count = 0
    protected_count = 0

    # Protect bookings with explicit 'protected' flag
    to_process = []
    for b in bookings:
        if b.get('protected', False):
            protected_count += 1
            continue
        to_process.append(b)

    # Process in batches to reduce memory/single op size
    BATCH = 200
    for i in range(0, len(to_process), BATCH):
        batch = to_process[i:i+BATCH]
        # Archive documents
        archive_docs = []
        for b in batch:
            archived = {
                'original_id': b.get('_id'),
                'archived_at': _dt.utcnow(),
                'archived_by': getattr(current_user, 'email', getattr(current_user, 'id', 'unknown')),
                'doc': b
            }
            archive_docs.append(archived)
        if archive_docs:
            db.deletion_archive.insert_many(archive_docs)
        # Delete originals
        ids = [b.get('_id') for b in batch]
        res = db.bookings.delete_many({'_id': {'$in': ids}})
        archived_count += res.deleted_count
        deleted_ids.extend([str(x) for x in ids])

    # After deletion, ensure raft occupancy is reset as before for this date
    from utils.booking_ops import get_deallocation_amounts
    freed_count = 0
    for b in bookings:
        if b.get('status') == 'Confirmed' and b.get('raft_allocations'):
            raft_ids = b.get('raft_allocations', [])
            group_size = int(b.get('group_size', 0))
            booking_date = b.get('date')
            booking_slot = b.get('slot')
            if raft_ids and group_size > 0:
                deallocations = get_deallocation_amounts(db, booking_date, booking_slot, group_size, raft_ids)
                for raft_id, amount_to_remove in deallocations:
                    raft = db.rafts.find_one({'day': booking_date, 'slot': booking_slot, 'raft_id': raft_id})
                    if not raft:
                        continue
                    current_occupancy = max(0, raft.get('occupancy', 0))
                    new_occupancy = max(0, current_occupancy - amount_to_remove)
                    update_data = {'$set': {'occupancy': new_occupancy}}
                    if new_occupancy == 0:
                        update_data['$set']['is_special'] = False
                    elif new_occupancy != 7:
                        update_data['$set']['is_special'] = False
                    db.rafts.update_one({'day': booking_date, 'slot': booking_slot, 'raft_id': raft_id}, update_data)
                freed_count += 1

    # Clean up raft occupancy as before
    db.rafts.update_many({'day': date, 'occupancy': {'$lt': 0}}, {'$set': {'occupancy': 0, 'is_special': False}})
    db.rafts.update_many({'day': date, '$or': [{'occupancy': {'$lte': 0}}, {'occupancy': {'$exists': False}}]}, {'$set': {'occupancy': 0, 'is_special': False}})
    remaining_bookings = db.bookings.count_documents({'date': date})
    if remaining_bookings == 0:
        db.rafts.update_many({'day': date}, {'$set': {'occupancy': 0, 'is_special': False}})

    # Create audit entry
    audit = {
        'action': 'delete_bookings_by_date',
        'date_range': {'from': date, 'to': date},
        'requested_by': getattr(current_user, 'email', getattr(current_user, 'id', 'unknown')),
        'requested_at': _dt.utcnow(),
        'deleted_count': archived_count,
        'protected_count': protected_count,
        'freed_count': freed_count,
        'deleted_ids': deleted_ids,
        'request_meta': {
            'ip': request.remote_addr,
            'user_agent': request.headers.get('User-Agent')
        }
    }
    db.deletion_audits.insert_one(audit)

    return jsonify({
        'message': f'Successfully deleted {archived_count} booking(s) for {date}. Freed occupancy from {freed_count} confirmed booking(s). Protected: {protected_count}',
        'deleted_count': archived_count,
        'freed_count': freed_count,
        'protected_count': protected_count
    }), 200

@admin_bp.route('/occupancy_data')
@login_required
@subadmin_or_admin_required
def occupancy_data():
    from datetime import date as _date
    from models.raft_model import ensure_rafts_for_date_slot
    db = current_app.mongo.db
    settings = load_settings(db)
    slots = settings.get('time_slots', [])
    rafts_per_slot = settings.get('rafts_per_slot', 5)
    capacity = settings.get('capacity', 6)
    
    # Get day parameter
    qday = request.args.get('day')
    
    # Sub-Admin: Only allow single date selection, default to today if not provided
    if current_user.is_subadmin():
        if not qday:
            qday = _date.today().isoformat()
        # Validate that the date is a valid date string (security check)
        try:
            _date.fromisoformat(qday)
        except (ValueError, TypeError):
            qday = _date.today().isoformat()
        allowed_dates = [qday]
    else:
        # Admin: Use provided day parameter or default to today
        if not qday:
            qday = _date.today().isoformat()
        allowed_dates = [qday]
    
    # Ensure rafts exist for all slots with current settings for allowed dates
    for date_str in allowed_dates:
        for slot in slots:
            ensure_rafts_for_date_slot(db, date_str, slot, rafts_per_slot, capacity)
    
    result = {}
    qday = allowed_dates[0]  # Single date for both admin and subadmin
    
    # For both admin and subadmin, return data grouped by slot (single date)
    for slot in slots:
        # Fetch only the configured number of rafts (limit to rafts_per_slot)
        rafts = list(db.rafts.find({'slot': slot, 'day': qday}).sort('raft_id', 1).limit(rafts_per_slot))
        # Clamp occupancy to >= 0 and ensure is_special is only True if occupancy > 0
        result[slot] = [{
            'raft_id': r.get('raft_id', '?'), 
            'occupancy': max(0, r.get('occupancy', 0)), 
            'capacity': capacity,
            'is_special': r.get('is_special', False) and max(0, r.get('occupancy', 0)) > 0
        } for r in rafts[:rafts_per_slot]]
    return jsonify(result)

@admin_bp.route('/occupancy_by_date')
@login_required
@admin_required
def occupancy_by_date():
    from models.raft_model import ensure_rafts_for_date_slot
    db = current_app.mongo.db
    settings = load_settings(db)
    rafts_per_slot = settings.get('rafts_per_slot', 5)
    capacity = settings.get('capacity', 6)
    qday = request.args.get('day')
    rafts_query = {'day': qday} if qday else {}
    
    # If a specific day is requested, ensure rafts exist for all slots
    if qday:
        slots = settings.get('time_slots', [])
        for slot in slots:
            ensure_rafts_for_date_slot(db, qday, slot, rafts_per_slot, capacity)
    
    rafts = list(db.rafts.find(rafts_query).sort([('day',1), ('slot',1), ('raft_id',1)]))
    grouped = {}
    for r in rafts:
        day = r.get('day', 'Unknown')
        slot = r.get('slot', 'Unknown')
        
        # Initialize day and slot in grouped if not exists
        if day not in grouped:
            grouped[day] = {}
        if slot not in grouped[day]:
            grouped[day][slot] = []
        
        # Only add rafts up to the configured limit per slot
        if len(grouped[day][slot]) < rafts_per_slot:
            grouped[day][slot].append({
                'raft_id': r.get('raft_id', '?'),
                'occupancy': max(0, r.get('occupancy', 0)),
                'capacity': capacity
            })
    return jsonify(grouped)

@admin_bp.route('/occupancy_detail')
@login_required
@subadmin_or_admin_required
def occupancy_detail():
    from datetime import date as _date
    
    try:
        db = current_app.mongo.db
        settings = load_settings(db)
        slots = settings.get('time_slots', [])
        rafts_per_slot = settings.get('rafts_per_slot', 5)
        capacity = settings.get('capacity', 6)
        
        # Get day parameter
        qday = request.args.get('day')
        
        # Sub-Admin: Only allow single date selection, default to today if not provided
        if current_user.is_subadmin():
            if not qday:
                qday = _date.today().isoformat()
            # Validate that the date is a valid date string (security check)
            try:
                _date.fromisoformat(qday)
            except (ValueError, TypeError):
                qday = _date.today().isoformat()
            allowed_dates = [qday]
        else:
            # Admin: Use provided day parameter or default to today
            if not qday:
                qday = _date.today().isoformat()
            allowed_dates = [qday]
        
        result = {}

        bookings_by_slot = {}
        bookings_count = 0
        # Build booking query and fetch bookings for allowed dates only
        booking_query = {'date': {'$in': allowed_dates}}
        # Optionally filter by status and slot if provided by admin
        try:
            filter_status = request.args.getlist('status') or []
            filter_slot = request.args.getlist('slot') or []
            if filter_status:
                if len(filter_status) == 1:
                    booking_query['status'] = filter_status[0]
                else:
                    booking_query['status'] = {'$in': filter_status}
            if filter_slot:
                if len(filter_slot) == 1:
                    booking_query['slot'] = filter_slot[0]
                else:
                    booking_query['slot'] = {'$in': filter_slot}
        except Exception:
            pass

        for b in db.bookings.find(booking_query):
            s = b.get('slot')
            date_key = b.get('date')
            if date_key not in bookings_by_slot:
                bookings_by_slot[date_key] = {}
            bookings_by_slot[date_key].setdefault(s, []).append(b)
            bookings_count += 1
        
        # Ensure rafts exist for all slots with current settings for all allowed dates
        from models.raft_model import ensure_rafts_for_date_slot
        for date_str in allowed_dates:
            for slot in slots:
                ensure_rafts_for_date_slot(db, date_str, slot, rafts_per_slot, capacity)
        
        # Process each allowed date
        for date_str in allowed_dates:
            # If no bookings exist for this date, ensure all rafts are reset to clean state
            date_bookings = sum(len(slots_dict) for slots_dict in bookings_by_slot.get(date_str, {}).values())
            if date_bookings == 0:
                # Clean up any negative occupancy values and clear special flags
                db.rafts.update_many(
                    {'day': date_str, '$or': [
                        {'occupancy': {'$lt': 0}},
                        {'occupancy': {'$gt': 0}},
                        {'is_special': True}
                    ]},
                    {'$set': {'occupancy': 0, 'is_special': False}}
                )
            
            # Remove extra rafts beyond configured count (only if they have no occupancy)
            for slot in slots:
                existing_rafts = list(db.rafts.find({'day': date_str, 'slot': slot}).sort('raft_id', 1))
                if len(existing_rafts) > rafts_per_slot:
                    # Remove rafts beyond the configured limit (only if they have no occupancy)
                    for raft in existing_rafts[rafts_per_slot:]:
                        if raft.get('occupancy', 0) == 0:
                            db.rafts.delete_one({'_id': raft['_id']})

            # Build result for this date
            for slot in slots:
                # Fetch only the configured number of rafts (limit to rafts_per_slot)
                rafts = list(db.rafts.find({'day': date_str, 'slot': slot}).sort('raft_id', 1).limit(rafts_per_slot))
                raft_list = []
                for r in rafts:
                    raft_bookings = []
                    slot_bookings = bookings_by_slot.get(date_str, {}).get(slot, [])
                    for b in slot_bookings:
                        if b.get('raft_allocations') and r.get('raft_id') in b.get('raft_allocations', []):
                            raft_bookings.append({
                                'id': str(b['_id']),
                                'name': b.get('user_name') or b.get('name') or '',
                                'email': b.get('email',''),
                                'group_size': b.get('group_size'),
                                'status': b.get('status')
                            })
                    # Only show is_special if occupancy > 0 (special rafts should have occupancy)
                    # Clamp occupancy to >= 0 to prevent negative values
                    occupancy = max(0, r.get('occupancy', 0))
                    is_special = r.get('is_special', False) and occupancy > 0
                    
                    # If occupancy is 0 but is_special is True in DB, fix it (data consistency)
                    if occupancy == 0 and r.get('is_special', False):
                        db.rafts.update_one(
                            {'_id': r['_id']},
                            {'$set': {'is_special': False}}
                        )
                        is_special = False
                    
                    raft_list.append({
                        'raft_id': r.get('raft_id'),
                        'occupancy': occupancy,
                        'capacity': capacity,
                        'is_special': is_special,
                        'bookings': raft_bookings
                    })
                
                # Both admin and subadmin: group by slot only (single date)
                result[slot] = raft_list[:rafts_per_slot]
        return jsonify(result)
    except Exception as e:
        print(f'[ERROR] occupancy_detail: {str(e)}')
        # Return empty data instead of error to allow graceful fallback on frontend
        return jsonify({}), 200


@admin_bp.route('/export_occupancy_pdf')
@login_required
@subadmin_or_admin_required
def export_occupancy_pdf():
    """Generate a PDF report for occupancy overview.
    Supports admin range exports (from_date/to_date) and subadmin single-date exports (day).
    Accepts optional `slot` and `status` query params (multi-value).
    """
    try:
        import io
        from reportlab.lib.pagesizes import A4
        from reportlab.lib import colors
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
        from reportlab.lib.units import mm
        import datetime as _dt
    except Exception as imp_err:
        print(f'[ERROR] PDF generation imports: {imp_err}')
        return jsonify({'error': 'PDF generation not available on server (missing dependency)'}), 500

    db = current_app.mongo.db
    settings = load_settings(db)
    slots = settings.get('time_slots', [])
    rafts_per_slot = settings.get('rafts_per_slot', 5)
    default_capacity = settings.get('capacity', 6)

    # Determine allowed dates while respecting subadmin restrictions
    from_date = request.args.get('from_date')
    to_date = request.args.get('to_date')
    qday = request.args.get('day')

    allowed_dates = []
    if current_user.is_subadmin():
        # Sub-admin allowed only the viewed date; default to today if not provided
        if not qday:
            qday = _dt.date.today().isoformat()
        try:
            _dt.date.fromisoformat(qday)
        except Exception:
            qday = _dt.date.today().isoformat()
        allowed_dates = [qday]
    else:
        # Admin: support range or single day
        try:
            if from_date and to_date:
                fd = _dt.date.fromisoformat(from_date)
                td = _dt.date.fromisoformat(to_date)
                if fd > td:
                    fd, td = td, fd
                cur = fd
                while cur <= td:
                    allowed_dates.append(cur.isoformat())
                    cur = cur + _dt.timedelta(days=1)
            elif from_date:
                _dt.date.fromisoformat(from_date)
                allowed_dates = [from_date]
            elif to_date:
                _dt.date.fromisoformat(to_date)
                allowed_dates = [to_date]
            else:
                day = qday or _dt.date.today().isoformat()
                allowed_dates = [day]
        except Exception:
            # Fallback to today
            allowed_dates = [_dt.date.today().isoformat()]

    # Slot and status filters (slot restricts which slots are included in report)
    filter_slot = request.args.getlist('slot') or []
    filter_status = request.args.getlist('status') or []

    # Build PDF
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=18*mm, rightMargin=18*mm, topMargin=18*mm, bottomMargin=22*mm)
    styles = getSampleStyleSheet()
    normal = styles['Normal']
    elements = []

    # Title
    title_style = ParagraphStyle('title', parent=styles['Heading1'], alignment=1, fontSize=16)
    elements.append(Paragraph('Occupancy Overview Report', title_style))
    elements.append(Spacer(1, 6))

    # Filters & timestamp
    ts = _dt.datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')
    meta_lines = []
    if len(allowed_dates) == 1:
        meta_lines.append(f'Date: {allowed_dates[0]}')
    else:
        meta_lines.append(f'Date Range: {allowed_dates[0]} to {allowed_dates[-1]}')
    if filter_status:
        meta_lines.append('Status: ' + ', '.join(filter_status))
    if filter_slot:
        meta_lines.append('Slots: ' + ', '.join(filter_slot))
    meta_lines.append(f'Exported: {ts}')

    for line in meta_lines:
        elements.append(Paragraph(line, normal))
    elements.append(Spacer(1, 8))

    # For each date, build table with columns: Time Slot | Occupied | Available | Occupancy %
    from models.raft_model import ensure_rafts_for_date_slot

    any_data = False
    for date_str in allowed_dates:
        # Ensure rafts exist for configured slots
        for slot in slots:
            ensure_rafts_for_date_slot(db, date_str, slot, rafts_per_slot, default_capacity)

        # Decide which slots to include: either filter_slot or all configured slots
        slot_list = filter_slot if filter_slot else slots

        # Build rows
        rows = []
        for slot in slot_list:
            # fetch rafts for this date and slot
            rafts = list(db.rafts.find({'day': date_str, 'slot': slot}).sort('raft_id', 1).limit(rafts_per_slot))
            if not rafts:
                continue
            slot_occupancy = sum(max(0, r.get('occupancy', 0)) for r in rafts)
            slot_capacity = sum(default_capacity for _ in rafts)
            available = slot_capacity - slot_occupancy
            occupancy_pct = f"{(slot_occupancy / slot_capacity * 100):.1f}%" if slot_capacity > 0 else '0%'
            rows.append([slot, str(slot_occupancy), str(available), occupancy_pct])

        if not rows:
            elements.append(Paragraph(f'No occupancy data available for {date_str}', normal))
            elements.append(Spacer(1, 8))
            continue

        any_data = True
        # Add section header per date
        elements.append(Paragraph(f'Report Date: {date_str}', styles['Heading3']))
        elements.append(Spacer(1, 4))

        data_table = [['Time Slot', 'Occupied', 'Available', 'Occupancy %']] + rows
        table = Table(data_table, colWidths=[90*mm, 25*mm, 25*mm, 30*mm])
        table.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#428BCA')),
            ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
            ('ALIGN',(1,0),(-1,-1),'CENTER'),
            ('GRID', (0,0), (-1,-1), 0.5, colors.grey),
            ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
            ('BOTTOMPADDING', (0,0), (-1,0), 6),
        ]))
        elements.append(table)
        elements.append(Spacer(1, 8))

    if not any_data:
        # No data for any requested date(s)
        elements.append(Paragraph('No occupancy data available', normal))

    # Footer: page numbers and export timestamp
    def _add_page_number(canvas, doc):
        canvas.saveState()
        w, h = A4
        page_num_text = f'Page {canvas.getPageNumber()}'
        canvas.setFont('Helvetica', 8)
        canvas.drawCentredString(w/2.0, 12*mm, page_num_text)
        canvas.drawRightString(w - 18*mm, 12*mm, f'Exported: {ts}')
        canvas.restoreState()

    doc.build(elements, onFirstPage=_add_page_number, onLaterPages=_add_page_number)

    pdf = buffer.getvalue()
    buffer.close()

    # Send response with appropriate headers
    filename = f"Occupancy_Overview_{allowed_dates[0]}"
    if len(allowed_dates) > 1:
        filename = f"Occupancy_Overview_{allowed_dates[0]}_to_{allowed_dates[-1]}"
    if filter_slot:
        filename += '_' + '_'.join([s.replace(' ', '') for s in filter_slot])
    filename += f"_{_dt.datetime.utcnow().date().isoformat()}.pdf"

    from flask import Response
    resp = Response(pdf, mimetype='application/pdf')
    resp.headers['Content-Disposition'] = f'attachment; filename="{filename}"'
    return resp
    

@admin_bp.route('/cancel_booking/<booking_id>', methods=['POST'])
@login_required
@admin_required  # Only admin, not subadmin
def cancel_booking_route(booking_id):
    db = current_app.mongo.db
    try:
        oid = ObjectId(booking_id)
    except Exception:
        return jsonify({'error': 'Invalid booking id'}), 400
    print('Cancel called for', booking_id)
    res = cancel_booking(db, oid)
    return jsonify(res)

@admin_bp.route('/postpone_booking/<booking_id>', methods=['POST'])
@login_required
@admin_required  # Only admin, not subadmin
def postpone_booking_route(booking_id):
    db = current_app.mongo.db
    data = request.get_json() or {}
    new_date = data.get('new_date')
    new_slot = data.get('new_slot')
    if not new_date or not new_slot:
        return jsonify({'error': 'new_date and new_slot required'}), 400
    try:
        oid = ObjectId(booking_id)
    except Exception:
        return jsonify({'error': 'Invalid booking id'}), 400
    
    res = postpone_booking(db, oid, new_date, new_slot)
    
    # Return error response with appropriate status code
    if 'error' in res:
        return jsonify(res), 400
    
    return jsonify(res), 200


@admin_bp.route('/delete_bookings_preview', methods=['POST'])
@login_required
@admin_required
def delete_bookings_preview():
    """Preview bookings that would be deleted for a given date or date range.
    Request JSON: { from_date: 'YYYY-MM-DD', to_date: 'YYYY-MM-DD' }
    Returns counts, protected count, and sample rows (non-sensitive fields only).
    """
    db = current_app.mongo.db
    data = request.get_json() or {}
    from_date = data.get('from_date')
    to_date = data.get('to_date')

    # normalize
    if from_date and not to_date:
        to_date = from_date
    if to_date and not from_date:
        from_date = to_date

    if not from_date:
        return jsonify({'error': 'from_date or to_date required'}), 400

    try:
        # validate
        from datetime import date as _date
        _ = _date.fromisoformat(from_date)
        _ = _date.fromisoformat(to_date)
    except Exception:
        return jsonify({'error': 'Invalid date format. Use YYYY-MM-DD'}), 400

    # Query bookings (exclude already archived/deleted)
    query = {'date': {'$gte': from_date, '$lte': to_date}, 'deleted': {'$ne': True}}
    total = db.bookings.count_documents(query)
    # Protected items
    protected_query = {'date': {'$gte': from_date, '$lte': to_date}, 'protected': True}
    protected_count = db.bookings.count_documents(protected_query)

    # Get small sample of non-sensitive fields
    sample_cursor = db.bookings.find(query, {'_id': 1, 'user_name': 1, 'email': 1, 'date':1, 'slot':1, 'group_size':1, 'status':1}).limit(10)
    samples = []
    for s in sample_cursor:
        samples.append({
            'id': str(s.get('_id')),
            'user_name': s.get('user_name') or s.get('name') or '',
            'email': s.get('email',''),
            'date': s.get('date'),
            'slot': s.get('slot'),
            'group_size': s.get('group_size'),
            'status': s.get('status')
        })

    return jsonify({
        'total': total,
        'protected_count': protected_count,
        'sample': samples,
        'range': {'from': from_date, 'to': to_date}
    }), 200


@admin_bp.route('/delete_bookings_execute', methods=['POST'])
@login_required
@admin_required
def delete_bookings_execute():
    """Execute deletion (archival + delete) for a date or date range.
    Request JSON: { from_date, to_date, confirm_text }
    confirm_text must exactly match the prompt string shown to user.
    """
    db = current_app.mongo.db
    data = request.get_json() or {}
    from_date = data.get('from_date')
    to_date = data.get('to_date')
    confirm_text = (data.get('confirm_text') or '').strip()

    if from_date and not to_date:
        to_date = from_date
    if to_date and not from_date:
        from_date = to_date

    if not from_date:
        return jsonify({'error': 'from_date or to_date required'}), 400

    try:
        from datetime import date as _date, datetime as _dt
        _ = _date.fromisoformat(from_date)
        _ = _date.fromisoformat(to_date)
    except Exception:
        return jsonify({'error': 'Invalid date format. Use YYYY-MM-DD'}), 400

    # Build expected confirmation phrase
    if from_date == to_date:
        expected = f"DELETE BOOKINGS FOR {from_date}"
    else:
        expected = f"DELETE BOOKINGS FROM {from_date} TO {to_date}"

    if confirm_text != expected:
        return jsonify({'error': 'Confirmation text does not match expected phrase', 'expected': expected}), 400

    # Query set to delete (exclude protected and already deleted)
    query = {'date': {'$gte': from_date, '$lte': to_date}, 'deleted': {'$ne': True}}
    total = db.bookings.count_documents(query)
    if total == 0:
        return jsonify({'message': 'No matching bookings to delete'}), 200

    # Identify protected items
    protected_q = {'date': {'$gte': from_date, '$lte': to_date}, 'protected': True}
    protected_count = db.bookings.count_documents(protected_q)

    # We'll archive into deletion_archive then delete originals in batches; record audit
    from datetime import datetime as _dt
    user_label = getattr(current_user, 'email', getattr(current_user, 'id', 'unknown'))
    deleted_ids = []
    archived_count = 0
    BATCH = 200

    cursor = db.bookings.find(query)
    docs = list(cursor)

    # Process in batches
    for i in range(0, len(docs), BATCH):
        batch = docs[i:i+BATCH]
        archive_docs = []
        ids = []
        for b in batch:
            if b.get('protected', False):
                continue
            archive_docs.append({'original_id': b.get('_id'), 'archived_at': _dt.utcnow(), 'archived_by': user_label, 'doc': b})
            ids.append(b.get('_id'))
        if archive_docs:
            db.deletion_archive.insert_many(archive_docs)
        if ids:
            res = db.bookings.delete_many({'_id': {'$in': ids}})
            archived_count += res.deleted_count
            deleted_ids.extend([str(x) for x in ids])

    # After deletion adjust raft occupancy for affected bookings (best-effort)
    from utils.booking_ops import get_deallocation_amounts
    freed_count = 0
    # For simplicity, recompute occupancy for affected dates/slots could be done; here we do best-effort dealloc
    for b in docs:
        if b.get('status') == 'Confirmed' and b.get('raft_allocations'):
            raft_ids = b.get('raft_allocations', [])
            group_size = int(b.get('group_size', 0))
            booking_date = b.get('date')
            booking_slot = b.get('slot')
            if raft_ids and group_size > 0:
                deallocations = get_deallocation_amounts(db, booking_date, booking_slot, group_size, raft_ids)
                for raft_id, amount_to_remove in deallocations:
                    raft = db.rafts.find_one({'day': booking_date, 'slot': booking_slot, 'raft_id': raft_id})
                    if not raft:
                        continue
                    current_occupancy = max(0, raft.get('occupancy', 0))
                    new_occupancy = max(0, current_occupancy - amount_to_remove)
                    update_data = {'$set': {'occupancy': new_occupancy}}
                    if new_occupancy == 0:
                        update_data['$set']['is_special'] = False
                    elif new_occupancy != 7:
                        update_data['$set']['is_special'] = False
                    db.rafts.update_one({'day': booking_date, 'slot': booking_slot, 'raft_id': raft_id}, update_data)
                freed_count += 1

    # Insert audit entry
    audit = {
        'action': 'delete_bookings_execute',
        'range': {'from': from_date, 'to': to_date},
        'requested_by': user_label,
        'requested_at': _dt.utcnow(),
        'deleted_count': archived_count,
        'protected_count': protected_count,
        'freed_count': freed_count,
        'deleted_ids': deleted_ids,
        'request_meta': {'ip': request.remote_addr, 'user_agent': request.headers.get('User-Agent')}
    }
    db.deletion_audits.insert_one(audit)

    return jsonify({'message': f'Deletion completed. Deleted: {archived_count}, protected: {protected_count}', 'deleted_count': archived_count, 'protected_count': protected_count}), 200
