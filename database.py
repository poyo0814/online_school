import psycopg2
import bcrypt
from datetime import datetime
from functools import wraps
from flask import session, redirect, url_for

class Database:
    def __init__(self, app):
        self.app = app
        self.conn = psycopg2.connect(
            dbname=app.config['DB_NAME'],
            user=app.config['DB_USER'],
            password=app.config['DB_PASSWORD'],
            host=app.config['DB_HOST']
        )
    
    def execute(self, query, params=None, fetch=False):
        with self.conn.cursor() as cursor:
            try:
                cursor.execute(query, params or ())
                if fetch:
                    return cursor.fetchall()
                self.conn.commit()
                return True
            except Exception as e:
                self.conn.rollback()
                raise e

def role_required(*roles):
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            if 'role' not in session or session['role'] not in roles:
                return redirect(url_for('login'))
            return f(*args, **kwargs)
        return wrapped
    return decorator