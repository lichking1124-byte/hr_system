"""Emergency account recovery (run on your own computer, never on the web).

Resets a password, or creates an HRMO Administrator if the username doesn't exist.
Needs DATABASE_URL (it is read from your .env file).

    python reset_admin.py
"""
import getpass

from dotenv import load_dotenv

load_dotenv()

import database  # noqa: E402
from passwords import hash_password, password_problem  # noqa: E402


def main():
    username = input("Username to reset/create [admin]: ").strip().lower() or "admin"
    while True:
        pw = getpass.getpass("New password: ")
        problem = password_problem(pw, username)
        if problem:
            print(" ->", problem)
            continue
        if pw != getpass.getpass("Repeat password: "):
            print(" -> Passwords do not match.")
            continue
        break

    conn = database.get_db()
    existing = conn.execute("SELECT id FROM admin_users WHERE username = ?", (username,)).fetchone()
    if existing:
        conn.execute(
            "UPDATE admin_users SET password = ?, is_active = 1, must_change_password = 0 WHERE id = ?",
            (hash_password(pw), existing["id"]),
        )
        print(f"Password updated for '{username}'.")
    else:
        conn.execute(
            "INSERT INTO admin_users (username, password, role, full_name, is_active, must_change_password) "
            "VALUES (?, ?, 'hrmo', ?, 1, 0)",
            (username, hash_password(pw), username.title()),
        )
        print(f"Created HRMO Administrator '{username}'.")
    conn.commit()
    conn.close()


if __name__ == "__main__":
    main()
