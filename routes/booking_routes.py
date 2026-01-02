from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, current_app
from datetime import date, datetime, timedelta
from models.booking_model import create_booking, find_latest_by_contact
from utils.allocation_logic import allocate_raft, load_settings
from utils.amount_calculator import calculate_total_amount
from models.raft_model import ensure_rafts_for_date_slot
from bson.objectid import ObjectId
from datetime import timedelta as _timedelta

booking_bp = Blueprint('booking', __name__)

def get_settings(db):
    """Get settings, using cache if available, otherwise load from DB."""
    # Always try to get from cache first for performance
    settings = current_app.config.get('SETTINGS_CACHE')
    if settings:
        return settings
    # If cache is empty, load from DB and cache it
    settings = load_settings(db)
    current_app.config['SETTINGS_CACHE'] = settings
    return settings

@booking_bp.route('/')
def home():
    settings = get_settings(current_app.mongo.db)
    return render_template('home.html', settings=settings)

@booking_bp.route('/book', methods=['GET','POST'])
def book():
    db = current_app.mongo.db
    settings = get_settings(db)
    
    # Determine allowed booking window based on admin settings (start_date and end_date)
    today = date.today()
    
    # Get start_date and end_date from settings
    start_date_str = settings.get('start_date')
    end_date_str = settings.get('end_date')
    
    # If dates are not set, fall back to old behavior (backward compatibility)
    if start_date_str and end_date_str:
        try:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
        except (ValueError, TypeError):
            # Fallback to old calculation if date parsing fails
            number_of_booking_days = settings.get('days', 30)
            start_date = today
            end_date = today + timedelta(days=number_of_booking_days)
    else:
        # Backward compatibility: calculate from days
        number_of_booking_days = settings.get('days', 30)
        start_date = today
        end_date = today + timedelta(days=number_of_booking_days)
    
    # Ensure min_date is not before today (users can't book in the past)
    min_date = max(start_date, today)
    max_date = end_date

    if request.method == 'POST':
        name = request.form.get('name')
        email = request.form.get('email')
        phone = request.form.get('phone')
        booking_date_str = request.form.get('booking_date')
        slot = request.form.get('slot')
        # validate booking date exists and is a proper future date
        if not booking_date_str:
            flash('Please provide a booking date.', 'error')
            return redirect(url_for('booking.book'))
        try:
            booking_date = datetime.strptime(booking_date_str, '%Y-%m-%d').date()
        except Exception:
            flash('Invalid booking date format.', 'error')
            return redirect(url_for('booking.book'))
        # Validate booking date is within allowed window [min_date, max_date]
        if booking_date < min_date or booking_date > max_date:
            flash(f'Booking date must be between {min_date.isoformat()} and {max_date.isoformat()} (inclusive).', 'error')
            return redirect(url_for('booking.book'))

        # NEW: Logic to prevent booking past slots for TODAY
        # If booking_date is today, we must ensure the slot hasn't passed.
        if booking_date == today:
            now = datetime.now()
            # Simple parser for slot start time (e.g. "7:00–9:00" -> 7, "15:30..." -> 15.5)
            # This logic should match client-side parsing or be robust.
            def parse_slot_start_hour_minutes(s):
                # extracts "07:00" or "15:30"
                # split by first non-digit char if not a colon? 
                # Assuming format like "7:00-9:00" or "13:00-15:00"
                try:
                    # Clean string to get the start part
                    start_str = s.split('–')[0].split('-')[0].split('to')[0].strip()
                    # Parse "7:00" or "7"
                    if ':' in start_str:
                        hour, minute = map(int, start_str.split(':')[:2])
                    else:
                        hour = int(start_str)
                        minute = 0
                    
                    # Handle PM logic if manual pm text exists, but typically our settings use 24h or clear format.
                    # If the settings use 12h format (e.g. "3:30 PM"), we need to handle that.
                    is_pm = 'pm' in start_str.lower()
                    if is_pm and hour != 12:
                        hour += 12
                    
                    return hour, minute
                except:
                    return 0, 0 # Fallback, allow if parse fails (risky but better than blocking valid)

            slot_h, slot_m = parse_slot_start_hour_minutes(slot)
            
            # If current time > slot start time, block it. 
            # (Optionally add buffer, e.g. forbid booking 30 mins before)
            if now.hour > slot_h or (now.hour == slot_h and now.minute > slot_m):
                flash('Cannot book a time slot that has already started or passed.', 'error')
                return redirect(url_for('booking.book'))


        try:
            group_size = int(request.form.get('group_size'))
        except:
            flash('Invalid group size', 'error')
            return redirect(url_for('booking.book'))
        # Calculate max_people_per_slot dynamically: rafts_per_slot * (capacity + 1)
        # The +1 accounts for special 7-person rafts when capacity is 6
        max_people_per_slot = settings.get('rafts_per_slot', 5) * (settings.get('capacity', 6) + 1)
        if group_size < 1 or group_size > max_people_per_slot:
            flash(f'Invalid group size. Maximum allowed is {max_people_per_slot} people per slot.', 'error')
            return redirect(url_for('booking.book'))
        # Use the booking_date_str (YYYY-MM-DD) when interacting with raft helpers and DB
        # Server-side validation: reject if entire date is fully booked
        def is_date_fully_booked(db, date_str, settings):
            slots = settings.get('time_slots', [])
            rafts_per_slot = settings.get('rafts_per_slot', 5)
            capacity = settings.get('capacity', 6)
            from models.raft_model import ensure_rafts_for_date_slot as _ensure
            for s in slots:
                # ensure rafts present
                _ensure(db, date_str, s, rafts_per_slot, capacity)
                rafts = list(db.rafts.find({'day': date_str, 'slot': s}).sort('raft_id', 1).limit(rafts_per_slot))
                # compute vacancy using allocation rules (must match allocation logic)
                total_vacancy = 0
                for r in rafts:
                    occupancy = r.get('occupancy', 0)
                    is_special = r.get('is_special', False)
                    if occupancy == 0:
                        total_vacancy += capacity + 1
                    elif is_special:
                        total_vacancy += 0
                    else:
                        total_vacancy += max(capacity - occupancy, 0)
                # if any slot has vacancy, date is NOT fully booked
                if total_vacancy > 0:
                    return False
            return True

        if is_date_fully_booked(db, booking_date_str, settings):
            flash('Selected date is fully booked', 'error')
            return redirect(url_for('booking.book'))

        ensure_rafts_for_date_slot(db, booking_date_str, slot, settings['rafts_per_slot'], settings['capacity'])
        result = allocate_raft(db, None, booking_date_str, slot, group_size)
        
        # Calculate amount for this booking
        amount_calc = calculate_total_amount(settings, booking_date_str, group_size)
        amount_per_person = amount_calc['applicable_amount']
        total_amount = amount_calc['total_amount']
        
        if result.get('status') == 'Confirmed':
            booking_id = create_booking(db, name, email, phone, booking_date_str, slot, group_size, 
                                       status='Confirmed', raft_allocations=result.get('rafts', []),
                                       amount_per_person=amount_per_person, total_amount=total_amount)
            flash(result.get('message', 'Booking Confirmed!'), 'success')
        else:
            booking_id = create_booking(db, name, email, phone, booking_date_str, slot, group_size, 
                                       status='Pending', raft_allocations=[],
                                       amount_per_person=amount_per_person, total_amount=total_amount)
            flash(result.get('message', 'Booking Pending – admin will contact you.'), 'warning')
        return redirect(url_for('booking.booking_confirmation', booking_id=booking_id))
    # For GET requests, provide the min_date and max_date so the frontend datepicker can enforce range
    return render_template('booking.html', settings=settings, min_date=min_date.isoformat(), max_date=max_date.isoformat(), start_date=start_date.isoformat(), end_date=end_date.isoformat())

@booking_bp.route('/booking/<booking_id>/confirmation')
def booking_confirmation(booking_id):
    db = current_app.mongo.db
    try:
        b = db.bookings.find_one({'_id': ObjectId(booking_id)})
    except:
        b = None
    if not b:
        flash('Booking not found', 'error')
        return redirect(url_for('booking.home'))
    return render_template('booking_confirmation.html', booking=b)

@booking_bp.route('/availability')
def availability():
    """Get availability data - uses fresh settings to ensure accuracy."""
    db = current_app.mongo.db
    settings = get_settings(db)  # Uses cache if available, otherwise loads from DB
    slots = settings.get('time_slots', [])
    total_capacity = settings['rafts_per_slot'] * settings['capacity']
    data = {}
    for slot in slots:
        rafts = list(db.rafts.find({'slot': slot, 'day': {'$exists': True}}))
        total_occupancy = sum(r.get('occupancy',0) for r in rafts)
        available = max(total_capacity - total_occupancy, 0)
        percent_full = round((total_occupancy / total_capacity) * 100, 2) if total_capacity>0 else 0
        data[slot] = {'available': available, 'percent_full': percent_full}
    return jsonify(data)


@booking_bp.route('/slot_availability')
def slot_availability():
    """Return availability info for a specific day. Query param: day=YYYY-MM-DD
    Response: { slot1: { available: N, full: bool }, slot2: {...} }
    """
    db = current_app.mongo.db
    settings = get_settings(db)
    slots = settings.get('time_slots', [])
    rafts_per_slot = settings.get('rafts_per_slot', 5)
    capacity = settings.get('capacity', 6)

    day = request.args.get('day')
    if not day:
        return jsonify({}), 400

    res = {}
    from models.raft_model import ensure_rafts_for_date_slot as _ensure
    try:
        # Check if day is today to filter passed slots
        is_today = (day == date.today().isoformat())
        now = datetime.now()
        
        for s in slots:
            # Check time logic if today
            slot_passed = False
            if is_today:
                 try:
                    start_str = s.split('–')[0].split('-')[0].split('to')[0].strip()
                    if ':' in start_str:
                        hh, mm = map(int, start_str.split(':')[:2])
                    else:
                        hh = int(start_str)
                        mm = 0
                    if 'pm' in start_str.lower() and hh != 12: hh += 12
                    
                    if now.hour > hh or (now.hour == hh and now.minute > mm):
                        slot_passed = True
                 except:
                    pass

            # ensure rafts exist for this date/slot
            _ensure(db, day, s, rafts_per_slot, capacity)
            rafts = list(db.rafts.find({'day': day, 'slot': s}).sort('raft_id', 1).limit(rafts_per_slot))
            # compute available seats using same allocation rules
            total_capacity = 0
            total_occupancy = 0
            for r in rafts:
                r_capacity = r.get('capacity', capacity)
                total_capacity += r_capacity
                total_occupancy += max(0, r.get('occupancy', 0))
            # Note: special raft handling in allocation may allow +1 seat for empty rafts; approximate available
            available = max(total_capacity - total_occupancy, 0)
            
            # If slot passed, mark as full (0 available)
            if slot_passed:
                available = 0

            res[s] = {
                'available': available,
                'full': available <= 0
            }
    except Exception:
        return jsonify({}), 500

    return jsonify(res)


@booking_bp.route('/fully_booked_dates')
def fully_booked_dates():
    """Return a list of dates (ISO YYYY-MM-DD) within the booking window that are fully booked (all slots at 100%)."""
    db = current_app.mongo.db
    settings = get_settings(db)
    slots = settings.get('time_slots', [])
    rafts_per_slot = settings.get('rafts_per_slot', 5)
    capacity = settings.get('capacity', 6)

    # Determine date window (same logic as book view)
    today = date.today()
    start_date_str = settings.get('start_date')
    end_date_str = settings.get('end_date')
    if start_date_str and end_date_str:
        try:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
        except (ValueError, TypeError):
            start_date = today
            end_date = today + _timedelta(days=settings.get('days', 30))
    else:
        start_date = today
        end_date = today + _timedelta(days=settings.get('days', 30))

    # Helper matching allocation vacancy logic
    def date_fully_booked(db, date_str):
        from models.raft_model import ensure_rafts_for_date_slot as _ensure
        for s in slots:
            _ensure(db, date_str, s, rafts_per_slot, capacity)
            rafts = list(db.rafts.find({'day': date_str, 'slot': s}).sort('raft_id', 1).limit(rafts_per_slot))
            total_vacancy = 0
            for r in rafts:
                occupancy = r.get('occupancy', 0)
                is_special = r.get('is_special', False)
                if occupancy == 0:
                    total_vacancy += capacity + 1
                elif is_special:
                    total_vacancy += 0
                else:
                    total_vacancy += max(capacity - occupancy, 0)
            if total_vacancy > 0:
                return False
        return True

    fully = []
    cur = start_date
    while cur <= end_date:
        ds = cur.isoformat()
        try:
            if date_fully_booked(db, ds):
                fully.append(ds)
        except Exception:
            # ignore errors for a single day and continue
            pass
        cur = cur + _timedelta(days=1)

    return jsonify({'fully_booked_dates': fully})

@booking_bp.route('/track-booking', methods=['GET','POST'])
def track_booking():
    if request.method == 'POST':
        email = request.form.get('email')
        phone = request.form.get('phone')
        
        # Validate inputs
        if not email or not phone:
            flash('Please provide both email and phone number.', 'error')
            return redirect(url_for('booking.track_booking'))
        
        db = current_app.mongo.db
        # Updated to query ALL matches, not just latest
        cursor = find_latest_by_contact(db, email, phone) 
        # Note: find_latest_by_contact currently sorts and checks. 
        # Ideally, we should use a new function `find_all_by_contact` or reuse the cursor if it wasn't limited to 1.
        # Assuming `find_latest_by_contact` returns a mongo cursor (not find_one), we can iterate it. 
        # Let's inspect `models/booking_model.py` carefully or just write the find query here to be safe.
        
        bookings = list(db.bookings.find({
            '$or': [{'email': email}, {'phone': phone}]
        }).sort('created_at', -1))
        
        if not bookings:
            flash('No booking found for that contact.', 'error')
            return redirect(url_for('booking.track_booking'))
        
        # Convert ObjectId to string for template rendering
        for b in bookings:
            b['_id'] = str(b.get('_id'))
            
        return render_template('track_booking_result.html', bookings=bookings)
    return render_template('track_booking.html')
