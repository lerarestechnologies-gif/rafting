#!/usr/bin/env python
"""Quick test to verify MongoDB Atlas connection."""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from config import MONGO_URI
from pymongo import MongoClient

def test_connection():
    print("Testing MongoDB Atlas connection...")
    print(f"URI: {MONGO_URI[:50]}..." if MONGO_URI else "URI: Not set")
    
    if not MONGO_URI:
        print("[ERROR] MONGO_URI is not set in config.py")
        return False
    
    try:
        client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
        # Verify connection
        client.admin.command('ping')
        print("[SUCCESS] Connected to MongoDB Atlas!")
        
        # List databases
        dbs = client.list_database_names()
        print(f"\nAvailable databases ({len(dbs)}):")
        for db in dbs:
            print(f"  - {db}")
        
        # Check raft_booking database and collections
        db = client.raft_booking
        collections = db.list_collection_names()
        print(f"\nCollections in 'raft_booking' ({len(collections)}):")
        for col in collections:
            count = db[col].count_documents({})
            print(f"  - {col} ({count} documents)")
        
        return True
    except Exception as e:
        print(f"[ERROR] Connection failed: {str(e)}")
        print("\nTroubleshooting:")
        print("  1. Verify the URI is correct in config.py")
        print("  2. Ensure dnspython is installed: pip install dnspython")
        print("  3. Check your IP is in Atlas Network Access list")
        print("  4. Ensure your user credentials are correct in the URI")
        return False

if __name__ == "__main__":
    success = test_connection()
    sys.exit(0 if success else 1)
