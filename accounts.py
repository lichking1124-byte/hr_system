"""Admin account management (HRMO only) + own-password change + audit log viewer."""
import re
import secrets

from flask import Blueprint, render_template, request, redirect, url_for, flash, session

import database
from auth import ROLES, ROLE_HRMO, log_action, utcnow
from passwords import hash_password, verify_password, password_problem

accounts = Blueprint("accounts", __name__)

USERNAME_RE = re.compile(r"^[a-z0-9._-]{3,30}$")


def _active_hrmo_count(conn, excluding_id=None):
    q = "SELECT COUNT(*) AS n FROM admin_users WHERE role = 'hrmo' AND is_active = 1"
    params = ()
    if excluding_id is not None:
        q += " AND id <> ?"
        params = (excluding_id,)
    return conn.execute(q, params).fetchone()["n"]


def _temp_password():
    # letters + digits so it always satisfies the password policy
    return secrets.token_urlsafe(6).replace("-", "a").replace("_", "7") + "9x"


@accounts.route("/admin/users", methods=["GET", "POST"])
def users():
    conn = database.get_db()
    if request.method == "POST":
        username = request.form.get("username", "").strip().lower()
        full_name = request.form.get("full_name", "").strip()
        role = request.form.get("role", "")
        if not USERNAME_RE.match(username):
            flash("Username must be 3–30 characters: lowercase letters, numbers, dot, dash or underscore.")
        elif not full_name:
            flash("Full name is required.")
        elif role not in ROLES:
            flash("Please choose a valid role.")
        elif conn.execute("SELECT id FROM admin_users WHERE username = ?", (username,)).fetchone():
            flash("That username is already taken.")
        else:
            temp = _temp_password()
            conn.execute(
                "INSERT INTO admin_users (username, password, role, full_name, is_active, must_change_password) "
                "VALUES (?, ?, ?, ?, 1, 1)",
                (username, hash_password(temp), role, full_name),
            )
            conn.commit()
            log_action("user.create", "user", username, f"role={role}")
            flash(f"Account created for {username}. Temporary password (shown once): {temp}")
        conn.close()
        return redirect(url_for("accounts.users"))

    rows = conn.execute(
        "SELECT id, username, full_name, role, is_active, last_login, created_at FROM admin_users ORDER BY username"
    ).fetchall()
    conn.close()
    return render_template("admin/users.html", users=rows)


@accounts.route("/admin/users/<int:user_id>/toggle", methods=["POST"])
def toggle_user(user_id):
    conn = database.get_db()
    u = conn.execute("SELECT * FROM admin_users WHERE id = ?", (user_id,)).fetchone()
    if not u:
        conn.close()
        return "User not found", 404
    if u["id"] == session.get("user_id"):
        flash("You cannot deactivate your own account.")
    elif u["is_active"] and u["role"] == ROLE_HRMO and _active_hrmo_count(conn, excluding_id=u["id"]) == 0:
        flash("At least one active HRMO Administrator is required.")
    else:
        new_state = 0 if u["is_active"] else 1
        conn.execute("UPDATE admin_users SET is_active = ? WHERE id = ?", (new_state, user_id))
        conn.commit()
        log_action("user.activate" if new_state else "user.deactivate", "user", u["username"])
        flash(f"{u['username']} has been {'activated' if new_state else 'deactivated'}.")
    conn.close()
    return redirect(url_for("accounts.users"))


@accounts.route("/admin/users/<int:user_id>/role", methods=["POST"])
def set_role(user_id):
    role = request.form.get("role", "")
    conn = database.get_db()
    u = conn.execute("SELECT * FROM admin_users WHERE id = ?", (user_id,)).fetchone()
    if not u or role not in ROLES:
        conn.close()
        flash("Invalid request.")
        return redirect(url_for("accounts.users"))
    if u["id"] == session.get("user_id"):
        flash("You cannot change your own role.")
    elif u["role"] == ROLE_HRMO and role != ROLE_HRMO and _active_hrmo_count(conn, excluding_id=u["id"]) == 0:
        flash("At least one active HRMO Administrator is required.")
    else:
        conn.execute("UPDATE admin_users SET role = ? WHERE id = ?", (role, user_id))
        conn.commit()
        log_action("user.role_change", "user", u["username"], f"{u['role']} -> {role}")
        flash(f"{u['username']} is now {ROLES[role]}.")
    conn.close()
    return redirect(url_for("accounts.users"))


@accounts.route("/admin/users/<int:user_id>/reset-password", methods=["POST"])
def reset_password(user_id):
    conn = database.get_db()
    u = conn.execute("SELECT * FROM admin_users WHERE id = ?", (user_id,)).fetchone()
    if not u:
        conn.close()
        return "User not found", 404
    temp = _temp_password()
    conn.execute(
        "UPDATE admin_users SET password = ?, must_change_password = 1, password_changed_at = ? WHERE id = ?",
        (hash_password(temp), utcnow(), user_id),
    )
    conn.commit()
    conn.close()
    log_action("user.password_reset", "user", u["username"])
    flash(f"Temporary password for {u['username']} (shown once): {temp}")
    return redirect(url_for("accounts.users"))


@accounts.route("/admin/account", methods=["GET", "POST"])
def account():
    conn = database.get_db()
    user = conn.execute("SELECT * FROM admin_users WHERE id = ?", (session.get("user_id"),)).fetchone()
    if request.method == "POST":
        current = request.form.get("current_password", "")
        new = request.form.get("new_password", "")
        confirm = request.form.get("confirm_password", "")
        problem = password_problem(new, user["username"])
        if not verify_password(current, user["password"]):
            flash("Your current password is incorrect.")
            log_action("auth.password_change_failed")
        elif new != confirm:
            flash("The new passwords do not match.")
        elif problem:
            flash(problem)
        else:
            conn.execute(
                "UPDATE admin_users SET password = ?, must_change_password = 0, password_changed_at = ? WHERE id = ?",
                (hash_password(new), utcnow(), user["id"]),
            )
            conn.commit()
            conn.close()
            log_action("auth.password_changed")
            flash("Password updated.")
            return redirect(url_for("admin_home"))
    conn.close()
    return render_template("admin/account.html", user=user)


@accounts.route("/admin/audit")
def audit():
    q = request.args.get("q", "").strip().lower()
    conn = database.get_db()
    if q:
        like = f"%{q}%"
        rows = conn.execute(
            "SELECT * FROM audit_log WHERE LOWER(COALESCE(username,'')) LIKE ? OR LOWER(action) LIKE ? "
            "OR LOWER(COALESCE(details,'')) LIKE ? OR LOWER(COALESCE(target_id,'')) LIKE ? "
            "ORDER BY id DESC LIMIT 300",
            (like, like, like, like),
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT 300").fetchall()
    conn.close()
    return render_template("admin/audit.html", rows=rows, q=q)
