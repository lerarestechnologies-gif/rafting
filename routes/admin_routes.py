from bson.objectid import ObjectId
from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app, jsonify
from flask_login import login_required, current_user
from utils.allocation_logic import load_settings
from utils.booking_ops import cancel_booking, postpone_booking
from utils.settings_manager import invalidate_settings_cache, refresh_settings_cache, regenerate_rafts_for_settings_change
from models.booking_model import update_booking_status
import datetime

from datetime import timezone, timedelta

IST = timezone(timedelta(hours=5, minutes=30))

def utc_to_ist(dt):
    if not dt:
        return ""

    # ✅ FIX: handle old/naive UTC datetimes
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return dt.astimezone(IST).strftime("%d-%m-%Y %I:%M %p")




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
    # Build query filter for bookings list (filters apply only to bookings)
    query_filter = {}
    
    # Common sort order for all views: Date ASC, Slot ASC, then Created Desc
    # Note: Slot strings need to be sortable. If they are "7:00...", "10:00...", simple string sort works okayish but "7" comes after "10". 
    # For now we stick to DB string sort. Ideal world: sort by a separate 'slot_value'.
    sort_order = [('date', 1), ('slot', 1), ('created_at', -1)]

    # If subadmin, do not apply user-supplied filters — show Confirmed bookings for today and tomorrow separately
    if current_user.is_subadmin():
        today = datetime.date.today().isoformat()
        tomorrow = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
        
        # Fetch Today's Bookings
        bookings_today = list(db.bookings.find({'date': today, 'status': 'Confirmed'}).sort(sort_order))
        for b in bookings_today:
            b['created_at_ist'] = utc_to_ist(b.get('created_at'))
            
        # Fetch Tomorrow's Bookings
        bookings_tomorrow = list(db.bookings.find({'date': tomorrow, 'status': 'Confirmed'}).sort(sort_order))
        for b in bookings_tomorrow:
            b['created_at_ist'] = utc_to_ist(b.get('created_at'))

        return render_template('admin_dashboard.html',
                             bookings_today=bookings_today,
                             bookings_tomorrow=bookings_tomorrow,
                             is_subadmin=True,
                             settings=settings,
                             filter_from=today, # Default views for reference
                             filter_to=tomorrow,
                             filter_slot='',
                             filter_status='')

    # For admin: Read filter params from query string
    from_date = request.args.get('from', '').strip()
    to_date = request.args.get('to', '').strip()
    slot_filter = request.args.get('slot', '').strip()
    status_filter = request.args.get('status', '').strip()

    # Validate and build date range filter
    if from_date and to_date:
        try:
            f = datetime.date.fromisoformat(from_date)
            t = datetime.date.fromisoformat(to_date)
            if f > t:
                flash('From Date must not be later than To Date', 'error')
                # ignore date filters on invalid range
            else:
                query_filter['date'] = {'$gte': from_date, '$lte': to_date}
        except (ValueError, TypeError):
            flash('Invalid date format for filters', 'error')
    elif from_date:
        try:
            datetime.date.fromisoformat(from_date)
            query_filter['date'] = {'$gte': from_date}
        except (ValueError, TypeError):
            flash('Invalid From date', 'error')
    elif to_date:
        try:
            datetime.date.fromisoformat(to_date)
            query_filter['date'] = {'$lte': to_date}
        except (ValueError, TypeError):
            flash('Invalid To date', 'error')
    
    # If NO date filter is provided for Admin, default to TODAY
    if not from_date and not to_date:
        today_str = datetime.date.today().isoformat()
        query_filter['date'] = today_str
        # We set from/to variables so they appear in the UI inputs
        from_date = today_str
        to_date = today_str

    # Slot filter
    if slot_filter:
        query_filter['slot'] = slot_filter

    # Status filter
    if status_filter:
        query_filter['status'] = status_filter

    # Fetch bookings with filters (admin)
    bookings = list(db.bookings.find(query_filter).sort(sort_order).limit(500))
    for booking in bookings:
        booking['created_at_ist'] = utc_to_ist(booking.get('created_at'))

    # Render dashboard with current booking filters (admin)
    return render_template('admin_dashboard.html',
                         bookings=bookings,
                         is_subadmin=False,
                         settings=settings,
                         filter_from=from_date,
                         filter_to=to_date,
                         filter_slot=slot_filter,
                         filter_status=status_filter)

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
    
    # Find all bookings for the date
    bookings = list(db.bookings.find({'date': date}))
    
    if not bookings:
        return jsonify({'message': f'No bookings found for {date}'}), 200
    
    # Free up raft occupancy for confirmed bookings using allocation pattern logic
    from utils.booking_ops import cancel_booking, get_deallocation_amounts
    from bson.objectid import ObjectId
    
    freed_count = 0
    for booking in bookings:
        if booking.get('status') == 'Confirmed' and booking.get('raft_allocations'):
            # Use the same deallocation logic as cancel_booking
            raft_ids = booking.get('raft_allocations', [])
            group_size = int(booking.get('group_size', 0))
            booking_date = booking.get('date')
            booking_slot = booking.get('slot')
            
            if raft_ids and group_size > 0:
                # Get deallocation amounts using allocation pattern logic
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
                    
                    # Clear is_special flag following allocation logic rules
                    if new_occupancy == 0:
                        update_data['$set']['is_special'] = False
                    elif new_occupancy != 7:
                        update_data['$set']['is_special'] = False
                    
                    db.rafts.update_one(
                        {'day': booking_date, 'slot': booking_slot, 'raft_id': raft_id},
                        update_data
                    )
                
                freed_count += 1
    
    # Delete all bookings for the date
    result = db.bookings.delete_many({'date': date})
    deleted_count = result.deleted_count
    
    # Clean up all rafts for this date: reset negative occupancy, clear special flags for empty rafts
    # Fix any negative occupancy values (should never happen, but safety check)
    db.rafts.update_many(
        {'day': date, 'occupancy': {'$lt': 0}},
        {'$set': {'occupancy': 0, 'is_special': False}}
    )
    
    # Clear is_special flag and ensure occupancy is 0 for all rafts on this date with 0 or negative occupancy
    db.rafts.update_many(
        {'day': date, '$or': [{'occupancy': {'$lte': 0}}, {'occupancy': {'$exists': False}}]},
        {'$set': {'occupancy': 0, 'is_special': False}}
    )
    
    # Double-check: If no bookings exist for this date, reset all rafts to clean state
    remaining_bookings = db.bookings.count_documents({'date': date})
    if remaining_bookings == 0:
        # Reset all rafts for this date to clean state
        db.rafts.update_many(
            {'day': date},
            {'$set': {'occupancy': 0, 'is_special': False}}
        )
    
    return jsonify({
        'message': f'Successfully deleted {deleted_count} booking(s) for {date}. Freed occupancy from {freed_count} confirmed booking(s).',
        'deleted_count': deleted_count,
        'freed_count': freed_count
    }), 200


@admin_bp.route('/delete_records_by_date_range', methods=['POST'])
@login_required
@admin_required
def delete_records_by_date_range():
    """Delete bookings within a date range (inclusive). Admin only.

    Request JSON: { "from": "YYYY-MM-DD", "to": "YYYY-MM-DD" }
    Returns JSON with deleted_count and audit info.
    """
    db = current_app.mongo.db
    data = request.get_json() or {}
    from_date = (data.get('from') or '').strip()
    to_date = (data.get('to') or '').strip()

    # Validate presence
    if not from_date or not to_date:
        return jsonify({'error': 'Both from and to dates are required'}), 400

    # Validate format
    try:
        from datetime import datetime
        f = datetime.strptime(from_date, '%Y-%m-%d').date()
        t = datetime.strptime(to_date, '%Y-%m-%d').date()
    except Exception:
        return jsonify({'error': 'Invalid date format. Use YYYY-MM-DD'}), 400

    # Validate range
    if f > t:
        return jsonify({'error': 'From Date must not be later than To Date'}), 400

    # Enforce system start/end dates
    settings = load_settings(db)
    sys_start = settings.get('start_date')
    sys_end = settings.get('end_date')
    try:
        if sys_start:
            from datetime import datetime as _dt
            sd = _dt.strptime(sys_start, '%Y-%m-%d').date()
            if f < sd:
                return jsonify({'error': f'From date cannot be earlier than system start date {sys_start}'}), 400
        if sys_end:
            from datetime import datetime as _dt
            ed = _dt.strptime(sys_end, '%Y-%m-%d').date()
            if t > ed:
                return jsonify({'error': f'To date cannot be later than system end date {sys_end}'}), 400
    except Exception:
        # If settings dates malformed, deny to be safe
        return jsonify({'error': 'System date configuration invalid; aborting deletion'}), 400

    # Fetch bookings in range
    bookings_cursor = db.bookings.find({'date': {'$gte': from_date, '$lte': to_date}})
    bookings = list(bookings_cursor)
    if not bookings:
        return jsonify({'message': f'No bookings found between {from_date} and {to_date}', 'deleted_count': 0}), 200

    # Use booking_ops deallocation to free raft occupancy for confirmed bookings
    from utils.booking_ops import get_deallocation_amounts
    freed_count = 0
    for booking in bookings:
        if booking.get('status') == 'Confirmed' and booking.get('raft_allocations'):
            raft_ids = booking.get('raft_allocations', [])
            group_size = int(booking.get('group_size', 0) or 0)
            booking_date = booking.get('date')
            booking_slot = booking.get('slot')
            if raft_ids and group_size > 0:
                try:
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
                except Exception:
                    # continue on failure for individual bookings
                    continue

    # Delete bookings in range
    res = db.bookings.delete_many({'date': {'$gte': from_date, '$lte': to_date}})
    deleted_count = res.deleted_count

    # Post-cleanup of rafts (safety)
    db.rafts.update_many({'day': {'$gte': from_date, '$lte': to_date}, 'occupancy': {'$lt': 0}}, {'$set': {'occupancy': 0, 'is_special': False}})
    db.rafts.update_many({'day': {'$gte': from_date, '$lte': to_date}, '$or': [{'occupancy': {'$lte': 0}}, {'occupancy': {'$exists': False}}]}, {'$set': {'occupancy': 0, 'is_special': False}})

    # Audit log
    try:
        admin_id = None
        try:
            admin_id = current_user.get_id()
        except Exception:
            admin_id = getattr(current_user, 'id', None) or getattr(current_user, '_id', None) or getattr(current_user, 'email', None)
        audit = {
            'action': 'delete_records_by_date_range',
            'admin_id': str(admin_id),
            'admin_repr': getattr(current_user, 'email', '') or getattr(current_user, 'username', '') or str(admin_id),
            'from_date': from_date,
            'to_date': to_date,
            'deleted_count': deleted_count,
            'freed_confirmed_bookings': freed_count,
            'timestamp': datetime.datetime.utcnow()
        }
        db.admin_audit_logs.insert_one(audit)
    except Exception:
        # do not fail the operation if logging fails
        pass

    return jsonify({'message': f'Successfully deleted {deleted_count} booking(s) between {from_date} and {to_date}. Freed occupancy from {freed_count} confirmed booking(s).', 'deleted_count': deleted_count}), 200

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
    from datetime import date as _date, timedelta

    try:
        db = current_app.mongo.db
        settings = load_settings(db)
        slots = settings.get('time_slots', [])
        rafts_per_slot = settings.get('rafts_per_slot', 5)
        capacity = settings.get('capacity', 6)

        # Get from/to parameters (date range)
        from_date = request.args.get('from', '').strip()
        to_date = request.args.get('to', '').strip()

        # Default both empty -> today
        if not from_date and not to_date:
            today = _date.today().isoformat()
            from_date = today
            to_date = today

        # Validate dates
        try:
            f = _date.fromisoformat(from_date)
            t = _date.fromisoformat(to_date)
        except (ValueError, TypeError) as ex:
            return jsonify({'error': 'Invalid date format. Use YYYY-MM-DD'}), 400

        # Validate range
        if f > t:
            return jsonify({'error': 'From Date must not be later than To Date'}), 400

        # Build list of allowed dates (inclusive)
        allowed_dates = []
        cur = f
        while cur <= t:
            allowed_dates.append(cur.isoformat())
            cur = cur + timedelta(days=1)

        # Ensure rafts exist for all slots with current settings for allowed dates
        from models.raft_model import ensure_rafts_for_date_slot
        for date_str in allowed_dates:
            for slot in slots:
                ensure_rafts_for_date_slot(db, date_str, slot, rafts_per_slot, capacity)

        # Prepare bookings_by_slot_by_date (only for display of booking details related to rafts)
        bookings_by_slot = {}
        for b in db.bookings.find({'date': {'$gte': from_date, '$lte': to_date}}):
            s = b.get('slot')
            date_key = b.get('date')
            bookings_by_slot.setdefault(date_key, {}).setdefault(s, []).append(b)

        # Build result grouped by date -> slot -> raft_list
        result = {}
        for date_str in allowed_dates:
            result[date_str] = {}
            for slot in slots:
                rafts = list(db.rafts.find({'day': date_str, 'slot': slot}).sort('raft_id', 1).limit(rafts_per_slot))
                raft_list = []
                for r in rafts:
                    # Only show is_special if occupancy > 0
                    occupancy = max(0, r.get('occupancy', 0))
                    is_special = r.get('is_special', False) and occupancy > 0

                    # Fix inconsistent flags in DB (non-destructive)
                    if occupancy == 0 and r.get('is_special', False):
                        db.rafts.update_one({'_id': r['_id']}, {'$set': {'is_special': False}})
                        is_special = False

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

                    raft_list.append({
                        'raft_id': r.get('raft_id'),
                        'occupancy': occupancy,
                        'capacity': capacity,
                        'is_special': is_special,
                        'bookings': raft_bookings
                    })

                result[date_str][slot] = raft_list[:rafts_per_slot]

        return jsonify(result)
    except Exception as e:
        current_app.logger.error(f'[ERROR] occupancy_detail: {str(e)}')
        return jsonify({'error': 'Server error fetching occupancy'}), 500

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
    
    # Check current status before postponing
    # Pending bookings cannot be postponed (user must confirm/pay first)
    existing_booking = db.bookings.find_one({'_id': oid})
    if existing_booking and existing_booking.get('status') == 'Pending':
        return jsonify({'error': 'Pending bookings cannot be postponed. Please confirm the booking first.'}), 400
    
    res = postpone_booking(db, oid, new_date, new_slot)
    
    # Return error response with appropriate status code
    if 'error' in res:
        return jsonify(res), 400
    
    return jsonify(res), 200
