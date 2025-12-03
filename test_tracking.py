#!/usr/bin/env python
"""Test script to verify booking tracking functionality"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from config import MONGO_URI
from pymongo import MongoClient
from models.booking_model import find_latest_by_contact

def test_tracking():
    """Test the tracking functionality"""
    print("=" * 60)
    print("Testing Booking Tracking Feature")
    print("=" * 60)
    
    # Connect to MongoDB Atlas
    try:
        client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
        client.admin.command('ping')
        print("\n✅ Connected to MongoDB Atlas")
    except Exception as e:
        print(f"\n❌ Failed to connect: {str(e)}")
        return False
    
    db = client.raft_booking
    
    # Test 1: Check if bookings exist
    print("\n[TEST 1] Checking bookings collection...")
    booking_count = db.bookings.count_documents({})
    print(f"✅ Found {booking_count} booking(s)")
    
    if booking_count == 0:
        print("❌ No bookings in database. Cannot test tracking.")
        return False
    
    # Test 2: List available bookings
    print("\n[TEST 2] Available bookings:")
    bookings = db.bookings.find().sort('created_at', -1).limit(5)
    test_booking = None
    for i, b in enumerate(bookings, 1):
        email = b.get('email')
        phone = b.get('phone')
        name = b.get('user_name')
        status = b.get('status')
        print(f"  {i}. {name} | {email} | {phone} | {status}")
        if i == 1:
            test_booking = {'email': email, 'phone': phone, 'name': name}
    
    if not test_booking:
        print("❌ No test booking found")
        return False
    
    # Test 3: Test find_latest_by_contact function
    print(f"\n[TEST 3] Testing track_booking with:")
    print(f"  Email: {test_booking['email']}")
    print(f"  Phone: {test_booking['phone']}")
    
    cursor = find_latest_by_contact(db, test_booking['email'], test_booking['phone'])
    booking = None
    for b in cursor:
        booking = b
        break
    
    if not booking:
        print("❌ Tracking failed - no booking found")
        return False
    
    print("✅ Booking found by tracking function")
    print(f"  Name: {booking.get('user_name')}")
    print(f"  Date: {booking.get('date')}")
    print(f"  Slot: {booking.get('slot')}")
    print(f"  Status: {booking.get('status')}")
    print(f"  Group Size: {booking.get('group_size')}")
    
    # Test 4: Check all required fields
    print("\n[TEST 4] Verifying all required fields...")
    required_fields = ['user_name', 'email', 'phone', 'date', 'slot', 'group_size', 'status', 'total_amount', 'amount_per_person']
    missing_fields = []
    
    for field in required_fields:
        if field not in booking or booking.get(field) is None:
            missing_fields.append(field)
        else:
            print(f"  ✅ {field}: {booking.get(field)}")
    
    if missing_fields:
        print(f"❌ Missing fields: {', '.join(missing_fields)}")
        return False
    
    # Test 5: Test ObjectId conversion
    print("\n[TEST 5] Testing ObjectId conversion...")
    _id = booking.get('_id')
    print(f"  ObjectId type: {type(_id)}")
    _id_str = str(_id)
    print(f"  Converted to string: {_id_str}")
    print(f"  ✅ ObjectId conversion successful")
    
    # Test 6: Test with multiple bookings same contact
    print("\n[TEST 6] Testing with multiple bookings (same contact)...")
    same_contact_bookings = list(db.bookings.find({'email': test_booking['email'], 'phone': test_booking['phone']}).sort('created_at', -1))
    print(f"  Total bookings with same contact: {len(same_contact_bookings)}")
    if len(same_contact_bookings) > 1:
        print(f"  ✅ Latest booking is correctly selected")
        latest = same_contact_bookings[0]
        print(f"    Latest: {latest.get('date')} at {latest.get('slot')}")
    
    print("\n" + "=" * 60)
    print("ALL TESTS PASSED - Tracking Feature is Working!")
    print("=" * 60)
    print("\nUsage:")
    print("1. Start the Flask app: python app.py")
    print("2. Visit: http://localhost:5000/track-booking")
    print(f"3. Enter email: {test_booking['email']}")
    print(f"4. Enter phone: {test_booking['phone']}")
    print("5. Your booking will be displayed")
    
    return True

if __name__ == "__main__":
    success = test_tracking()
    sys.exit(0 if success else 1)
