import os
from types import SimpleNamespace
from flask import Flask, jsonify, current_app
from pymongo import MongoClient
from flask_login import LoginManager
from config import Config

client = None


def create_app():
    """Application factory. Connects to MongoDB using `pymongo` and registers blueprints."""
    global client
    app = Flask(__name__)
    app.config.from_object(Config)

    # Connect to MongoDB with optimized timeout settings for slow connections
    mongo_uri = app.config.get('MONGO_URI')
    client = MongoClient(mongo_uri, serverSelectionTimeoutMS=10000, socketTimeoutMS=10000, connectTimeoutMS=10000)
    # Try to get default database from URI, otherwise use raft_booking
    try:
        db = client.get_default_database()
        if db is None:
            db = client['raft_booking']
    except Exception:
        db = client['raft_booking']

    # Attach a small object with .db and .client for compatibility with existing code
    mongo_ns = SimpleNamespace()
    mongo_ns.client = client
    mongo_ns.db = db
    app.mongo = mongo_ns

    # Login manager
    login_manager = LoginManager(app)
    login_manager.login_view = 'auth.login'

    # health endpoint
    @app.route('/health')
    def health():
        try:
            # quick server check
            client.admin.command('ping')
            return jsonify({'status': 'ok', 'db': 'connected'})
        except Exception as e:
            return jsonify({'status': 'error', 'db': str(e)})

    # Jinja2 filter for phone number formatting
    @app.template_filter('format_phone')
    def format_phone(phone):
        if not phone:
            return '-'
        # Keep only digits
        digits = ''.join(filter(str.isdigit, str(phone)))
        
        # If 10 digits (common in India), format as +91 XXXXX XXXXX
        if len(digits) == 10:
            return f"+91 {digits[0:5]} {digits[5:10]}"
        elif len(digits) == 12 and digits.startswith('91'):
             return f"+{digits[0:2]} {digits[2:7]} {digits[7:12]}"
        
        # fallback
        return phone

    # user loader
    @login_manager.user_loader
    def load_user(user_id):
        from models.user_model import User
        try:
            user = User.find_by_id(app.mongo.db, user_id)
            if user and user.is_active():
                return user
            return None
        except Exception as e:
            print(f"[ERROR] Error loading user {user_id}: {str(e)}")
            return None

    # Register blueprints here (import inside factory to avoid circular imports)
    from routes.auth_routes import auth_bp
    from routes.booking_routes import booking_bp
    from routes.admin_routes import admin_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(booking_bp)
    app.register_blueprint(admin_bp)

    return app


app = create_app()


if __name__ == '__main__':
    debug_mode = os.environ.get('FLASK_DEBUG', 'False').lower() in ['true', '1', 't']
    app.run(debug=debug_mode, host='0.0.0.0', port=5000)
