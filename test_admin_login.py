#!/usr/bin/env python
"""Test script to verify admin login functionality with MongoDB Atlas"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from config import MONGO_URI
from pymongo import MongoClient
from werkzeug.security import check_password_hash

def test_admin_login():
    """Test that admin can log in with correct credentials"""
    print("=" * 60)
    print("Testing Admin Login with MongoDB Atlas")
    print("=" * 60)
    
    # Connect to MongoDB Atlas
    try:
        client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
        client.admin.command('ping')
        print("\n✅ Connected to MongoDB Atlas")
    except Exception as e:
        print(f"\n❌ Failed to connect to MongoDB Atlas: {str(e)}")
        return False
    
    db = client.raft_booking
    
    # Test 1: Check if users collection exists
    print("\n[TEST 1] Checking users collection...")
    collections = db.list_collection_names()
    if 'users' in collections:
        print("✅ Users collection exists")
    else:
        print("❌ Users collection does not exist")
        print(f"   Available collections: {collections}")
        return False
    
    # Test 2: Find admin user
    print("\n[TEST 2] Finding admin user...")
    admin_email = "admin123@gmail.com"
    admin_user = db.users.find_one({"email": admin_email})
    
    if not admin_user:
        print(f"❌ Admin user not found (email: {admin_email})")
        print(f"   Available users:")
        for user in db.users.find():
            print(f"     - {user.get('email')} (role: {user.get('role')})")
        return False
    
    print(f"✅ Admin user found")
    print(f"   Name: {admin_user.get('name')}")
    print(f"   Email: {admin_user.get('email')}")
    print(f"   Role: {admin_user.get('role')}")
    print(f"   Has password_hash: {bool(admin_user.get('password_hash'))}")
    
    # Test 3: Verify password
    print("\n[TEST 3] Verifying password...")
    password = "admin123"
    password_hash = admin_user.get('password_hash')
    
    if not password_hash:
        print("❌ No password hash found for admin user")
        return False
    
    if check_password_hash(password_hash, password):
        print(f"✅ Password verification successful")
        print(f"   Password '{password}' matches hash")
    else:
        print(f"❌ Password verification failed")
        print(f"   Password '{password}' does not match hash")
        return False
    
    # Test 4: Check role
    print("\n[TEST 4] Checking admin role...")
    if admin_user.get('role') == 'admin':
        print("✅ Admin role verified")
    else:
        print(f"❌ Invalid role: {admin_user.get('role')}")
        return False
    
    # Test 5: Check settings
    print("\n[TEST 5] Checking system settings...")
    if 'settings' not in collections:
        print("❌ Settings collection does not exist")
        return False
    
    settings = db.settings.find_one({"_id": "system_settings"})
    if not settings:
        print("❌ System settings not found")
        return False
    
    print("✅ System settings found")
    print(f"   Days: {settings.get('days')}")
    print(f"   Time slots: {settings.get('time_slots')}")
    print(f"   Rafts per slot: {settings.get('rafts_per_slot')}")
    print(f"   Capacity: {settings.get('capacity')}")
    
    # Test 6: Check sub-admin (optional)
    print("\n[TEST 6] Checking sub-admin user...")
    subadmin_email = "subadmin@gmail.com"
    subadmin_user = db.users.find_one({"email": subadmin_email})
    
    if subadmin_user:
        print(f"✅ Sub-admin user found")
        print(f"   Email: {subadmin_user.get('email')}")
        print(f"   Role: {subadmin_user.get('role')}")
    else:
        print(f"⚠️  Sub-admin user not found (optional)")
        print(f"   Run: python scripts/create_subadmin.py")
    
    print("\n" + "=" * 60)
    print("ALL TESTS PASSED ✅")
    print("=" * 60)
    print("\nAdmin login is properly configured with MongoDB Atlas!")
    print("\nLogin credentials:")
    print(f"  Email: {admin_email}")
    print(f"  Password: admin123")
    print("\nNext steps:")
    print("1. Start the Flask app: python app.py")
    print("2. Go to http://localhost:5000/login")
    print("3. Enter admin credentials")
    print("4. Access admin dashboard at http://localhost:5000/admin/dashboard")
    
    return True

if __name__ == "__main__":
    success = test_admin_login()
    sys.exit(0 if success else 1)
