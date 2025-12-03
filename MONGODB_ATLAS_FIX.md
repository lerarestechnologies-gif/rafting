# MongoDB Atlas Connection Fix - Admin Login & Admin Pages

## Problem
The admin login and admin pages were not connected to MongoDB Atlas because:
1. The `users` collection didn't exist in the MongoDB Atlas database
2. The `settings` collection didn't exist in the MongoDB Atlas database
3. The database initialization scripts (`init_db.py` and `create_subadmin.py`) were trying to connect to a local MongoDB instance instead of MongoDB Atlas

## Root Cause
- `config.py` contains the correct MongoDB Atlas URI: `mongodb+srv://raft_user:EZn1wZbMHZJY9Om4@rafting.plagdm1.mongodb.net/raft_booking`
- However, `init_db.py` and `create_subadmin.py` scripts were loading the MONGO_URI from a `.env` file (using `os.getenv()`)
- When the `.env` file didn't exist or had a different value, they fell back to `mongodb://localhost:27017/raft_booking` (local MongoDB)
- This caused the user and settings data to be created locally instead of in MongoDB Atlas

## Solution Implemented

### 1. Fixed `scripts/init_db.py`
**Before:**
```python
from dotenv import load_dotenv
import os
load_dotenv()
mongo_uri = os.getenv("MONGO_URI", "mongodb://localhost:27017/raft_booking")
```

**After:**
```python
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from config import MONGO_URI as mongo_uri
```

This ensures the script uses the correct MongoDB Atlas URI from `config.py`.

### 2. Fixed `scripts/create_subadmin.py`
Applied the same fix to ensure it connects to MongoDB Atlas.

### 3. Ran Database Initialization
```bash
python scripts/init_db.py
```

This created:
- ✅ `users` collection with admin user (email: `admin123@gmail.com`, password: `admin123`)
- ✅ `settings` collection with default system settings

## Verification
MongoDB Atlas now contains:
- **Collections**: bookings, rafts, settings, users
- **Users collection**: Contains admin user with proper role and hashed password
- **Settings collection**: Contains system configuration (days, slots, time_slots, etc.)

## How to Create Additional Admin/Sub-Admin Users

### Create Sub-Admin:
```bash
python scripts/create_subadmin.py
```
This creates a sub-admin user (email: `subadmin@gmail.com`, password: `subadmin123`)

### Create More Admin Users (Direct MongoDB):
You can manually insert users into MongoDB Atlas:
```python
from pymongo import MongoClient
from werkzeug.security import generate_password_hash
from config import MONGO_URI

client = MongoClient(MONGO_URI)
db = client.raft_booking

db.users.insert_one({
    "name": "Your Admin Name",
    "email": "your_email@example.com",
    "phone": "1234567890",
    "role": "admin",  # or "subadmin"
    "password_hash": generate_password_hash("your_password")
})
```

## Authentication Flow
1. User logs in at `/login` (in `routes/auth_routes.py`)
2. Email and password are verified against the `users` collection in MongoDB Atlas
3. User role is checked - must be `admin` or `subadmin`
4. Upon successful authentication, user is redirected to `/admin/dashboard`
5. Dashboard and other admin pages verify user role using decorators like `@admin_required` and `@subadmin_or_admin_required`

## Configuration Files
- `config.py`: Contains the MongoDB Atlas URI
- `app.py`: Initializes Flask app with MongoDB connection
- `routes/auth_routes.py`: Handles admin login
- `routes/admin_routes.py`: Handles admin pages and operations
- `models/user_model.py`: Defines User class for authentication

## Testing the Connection
To verify MongoDB Atlas connection:
```bash
python test_atlas_connection.py
```

Expected output:
```
[SUCCESS] Connected to MongoDB Atlas!
Available databases: raft_booking, admin, local
Collections in 'raft_booking': bookings, rafts, settings, users
```

## Default Admin Credentials
- **Email**: admin123@gmail.com
- **Password**: admin123

## Default Sub-Admin Credentials
- **Email**: subadmin@gmail.com
- **Password**: subadmin123

⚠️ **IMPORTANT**: Change these default passwords in a production environment!
