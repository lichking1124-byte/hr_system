import sqlite3
import os
from datetime import datetime

DB_PATH = "hr_system.db"

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS job_postings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            campus TEXT NOT NULL,
            title TEXT NOT NULL,
            description TEXT,
            skills TEXT,
            experience TEXT,
            education TEXT,
            other_requirements TEXT,
            required_documents TEXT,
            mandatory_requirements TEXT,
            is_active INTEGER DEFAULT 1,
            archived_at TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS applicants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER NOT NULL,
            full_name TEXT NOT NULL,
            email TEXT NOT NULL,
            phone TEXT,
            applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            screening_score INTEGER,
            screening_result TEXT,
            screening_reason TEXT,
            screening_missing TEXT,
            screening_strengths TEXT,
            screening_weaknesses TEXT,
            is_screened INTEGER DEFAULT 0,
            FOREIGN KEY (job_id) REFERENCES job_postings(id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS applicant_documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            applicant_id INTEGER NOT NULL,
            document_type TEXT NOT NULL,
            filename TEXT NOT NULL,
            filepath TEXT NOT NULL,
            uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (applicant_id) REFERENCES applicants(id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS admin_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    existing_admin = cursor.execute('SELECT * FROM admin_users WHERE username = ?', ('admin',)).fetchone()
    if not existing_admin:
        cursor.execute('INSERT INTO admin_users (username, password) VALUES (?, ?)', ('admin', 'bulsu2026'))

    conn.commit()
    conn.close()
    print("Database initialized!")