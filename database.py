import os
import psycopg2
import psycopg2.extras

DATABASE_URL = os.environ.get("DATABASE_URL")


class CursorWrapper:
    def __init__(self, cursor):
        self._cursor = cursor
        self.lastrowid = None

    def execute(self, query, params=None):
        if params is None:
            params = ()
        query = query.replace('?', '%s')

        stripped = query.strip().upper()
        is_insert = stripped.startswith("INSERT")
        has_returning = "RETURNING" in stripped

        if is_insert and not has_returning:
            query = query.rstrip().rstrip(';') + " RETURNING id"

        self._cursor.execute(query, params)

        if is_insert and not has_returning:
            row = self._cursor.fetchone()
            if row:
                self.lastrowid = row['id']

        return self

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()


class ConnectionWrapper:
    def __init__(self, conn):
        self._conn = conn

    def cursor(self):
        return CursorWrapper(self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor))

    def execute(self, query, params=None):
        cur = self.cursor()
        cur.execute(query, params)
        return cur

    def commit(self):
        self._conn.commit()

    def close(self):
        self._conn.close()


def get_db():
    conn = psycopg2.connect(DATABASE_URL)
    return ConnectionWrapper(conn)


def migrate_admin_accounts(cursor):
    """Hash any legacy plain-text passwords and seed the first administrator.

    - Accounts still using the old default password ('bulsu2026') are forced to
      choose a new password at next login.
    - Idempotent: safe to run on every startup."""
    from passwords import hash_password, is_hashed, DEFAULT_SEED_PASSWORD

    users = cursor.execute('SELECT id, username, password FROM admin_users').fetchall()
    for u in users:
        if not is_hashed(u['password']):
            must_change = 1 if u['password'] == DEFAULT_SEED_PASSWORD else 0
            cursor.execute(
                'UPDATE admin_users SET password = %s, must_change_password = %s WHERE id = %s',
                (hash_password(u['password']), must_change, u['id'])
            )
    if not users:
        cursor.execute(
            "INSERT INTO admin_users (username, password, role, full_name, must_change_password) "
            "VALUES (%s, %s, 'hrmo', 'HRMO Administrator', 1)",
            ('admin', hash_password(DEFAULT_SEED_PASSWORD))
        )
    cursor.execute("UPDATE admin_users SET role = 'hrmo' WHERE role IS NULL")
    cursor.execute("UPDATE admin_users SET is_active = 1 WHERE is_active IS NULL")


def init_db():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS job_postings (
            id SERIAL PRIMARY KEY,
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
            deadline DATE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cursor.execute('''
        ALTER TABLE job_postings ADD COLUMN IF NOT EXISTS deadline DATE
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS applicants (
            id SERIAL PRIMARY KEY,
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
            control_number TEXT,
            FOREIGN KEY (job_id) REFERENCES job_postings(id)
        )
    ''')
    # The applicants table already exists on Supabase, so CREATE TABLE IF NOT
    # EXISTS above won't add new columns to it — do that explicitly here.
    cursor.execute('''
        ALTER TABLE applicants ADD COLUMN IF NOT EXISTS control_number TEXT
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS applicant_documents (
            id SERIAL PRIMARY KEY,
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
            id SERIAL PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    # Role-based access control + account management columns
    for ddl in (
        "ALTER TABLE admin_users ADD COLUMN IF NOT EXISTS role TEXT DEFAULT 'hrmo'",
        "ALTER TABLE admin_users ADD COLUMN IF NOT EXISTS full_name TEXT",
        "ALTER TABLE admin_users ADD COLUMN IF NOT EXISTS is_active INTEGER DEFAULT 1",
        "ALTER TABLE admin_users ADD COLUMN IF NOT EXISTS must_change_password INTEGER DEFAULT 0",
        "ALTER TABLE admin_users ADD COLUMN IF NOT EXISTS last_login TIMESTAMP",
        "ALTER TABLE admin_users ADD COLUMN IF NOT EXISTS password_changed_at TIMESTAMP",
    ):
        cursor.execute(ddl)

    # Audit log (who did what, when, from where). Times are stored in UTC.
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS audit_log (
            id SERIAL PRIMARY KEY,
            username TEXT,
            role TEXT,
            action TEXT NOT NULL,
            target_type TEXT,
            target_id TEXT,
            details TEXT,
            ip_address TEXT,
            created_at TIMESTAMP NOT NULL
        )
    ''')
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_log (created_at)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_audit_action_user ON audit_log (action, username, created_at)")

    migrate_admin_accounts(cursor)
    conn.commit()
    conn.close()
    print("Database initialized!")