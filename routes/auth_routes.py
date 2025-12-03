from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app
from flask_login import login_user, logout_user, login_required
from models.user_model import User

auth_bp = Blueprint('auth', __name__)

@auth_bp.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        
        if not email or not password:
            flash('Email and password are required', 'error')
            return redirect(url_for('auth.login'))
        
        user_doc = current_app.mongo.db.users.find_one({'email': email})
        if not user_doc:
            flash('Invalid credentials', 'error')
            return redirect(url_for('auth.login'))
        
        user = User(user_doc)
        
        # Debug: Check what we have
        print(f"[DEBUG] Login attempt for email: {email}")
        print(f"[DEBUG] User found: {user.email}, Role: {user.role}")
        print(f"[DEBUG] Has password_hash: {bool(user.password_hash)}")
        
        # Check password first
        password_valid = user.check_password(password)
        print(f"[DEBUG] Password valid: {password_valid}")
        
        # Check role - allow both admin and subadmin
        role_valid = user.is_admin_or_subadmin()
        print(f"[DEBUG] Role valid (admin or subadmin): {role_valid}")
        print(f"[DEBUG] User role details: is_admin={user.is_admin()}, is_subadmin={user.is_subadmin()}")
        
        if password_valid and role_valid:
            try:
                login_user(user, remember=True)
                flash('Logged in successfully', 'success')
                print(f"[DEBUG] User {email} logged in successfully with role: {user.role}")
                next_page = request.args.get('next')
                # Redirect both admin and subadmin to dashboard
                return redirect(next_page or url_for('admin.dashboard'))
            except Exception as e:
                print(f"[ERROR] Login failed: {str(e)}")
                import traceback
                traceback.print_exc()
                flash('Login failed. Please try again.', 'error')
                return redirect(url_for('auth.login'))
        else:
            # More specific error message for debugging
            if not password_valid:
                flash('Invalid password', 'error')
            elif not role_valid:
                flash(f'Access denied: Invalid role (current role: {user.role})', 'error')
            else:
                flash('Invalid credentials or not authorized', 'error')
            return redirect(url_for('auth.login'))
    
    return render_template('login.html')

@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('booking.home'))
