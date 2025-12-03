from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, current_app
from datetime import date, datetime, timedelta
from models.booking_model import create_booking, find_latest_by_contact
from utils.allocation_logic import allocate_raft, load_settings
from utils.amount_calculator import calculate_total_amount
from models.raft_model import ensure_rafts_for_date_slot
from bson.objectid import ObjectId

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
        cursor = find_latest_by_contact(db, email, phone)
        booking = None
        for b in cursor:
            booking = b
            break
        if not booking:
            flash('No booking found for that contact.', 'error')
            return redirect(url_for('booking.track_booking'))
        
        # Convert ObjectId to string for template rendering
        booking['_id'] = str(booking.get('_id'))
        
        return render_template('track_booking_result.html', booking=booking)
    return render_template('track_booking.html')
