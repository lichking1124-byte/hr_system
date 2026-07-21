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
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
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
            FOREIGN KEY (job_id) REFERENCES job_postings(id)
        )
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
    existing_admin = cursor.execute('SELECT * FROM admin_users WHERE username = %s', ('admin',)).fetchone()
    if not existing_admin:
        cursor.execute('INSERT INTO admin_users (username, password) VALUES (%s, %s)', ('admin', 'bulsu2026'))
    conn.commit()
    conn.close()
    print("Database initialized!")