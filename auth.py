"""Authentication guard, role-based access control (RBAC), audit logging and
security headers for the admin side of the BulSU recruitment system."""
import os
from datetime import datetime, timedelta, timezone

from flask import request, session, redirect, url_for, flash, g, render_template

import database

# ── Roles ───────────────────────────────────────────────────────────────
ROLE_HRMO = "hrmo"        # HRMO Administrator: full access
ROLE_HRMPSB = "hrmpsb"    # HRMPSB Member: reviews applicants, no administration
ROLES = {ROLE_HRMO: "HRMO Administrator", ROLE_HRMPSB: "HRMPSB Member"}

# Default-deny for HRMPSB: only these endpoints are open to them.
# (HRMO may access everything; any new /admin endpoint is HRMO-only until listed here.)
HRMPSB_ALLOWED = {
    "admin_home", "admin_logout", "admin_applicants",
    "download_applicant_documents", "download_all_documents",
    "download_qualified", "download_all",
    "accounts.account",
}
# Reachable while a forced password change is pending
PASSWORD_CHANGE_ALLOWED = {"accounts.account", "admin_logout", "static"}

LOCKOUT_ATTEMPTS = 5
LOCKOUT_MINUTES = 15
PH_OFFSET = timedelta(hours=8)

# Endpoint -> (audit action, HTTP methods that count)
AUDIT_MAP = {
    "admin_new_job": ("job.create", ("POST",)),
    "admin_edit_job": ("job.update", ("POST",)),
    "admin_delete_job": ("job.archive_or_delete", ("POST",)),
    "admin_restore_job": ("job.restore", ("POST",)),
    "admin_screen": ("screening.run", ("GET",)),
    "admin_rescreen_all": ("screening.rescreen_all", ("GET",)),
    "admin_rescreen_one": ("screening.rescreen_one", ("GET",)),
    "admin_generate_ad": ("job.generate_ad", ("GET",)),
    "admin_applicants": ("applicants.view", ("GET",)),
    "download_applicant_documents": ("documents.download_one", ("GET",)),
    "download_all_documents": ("documents.download_all", ("GET",)),
    "download_qualified": ("export.qualified", ("GET",)),
    "download_all": ("export.all_results", ("GET",)),
}


def utcnow():
    """Naive UTC 'now' (the database stores UTC)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def client_ip():
    fwd = request.headers.get("X-Forwarded-For", "")
    return (fwd.split(",")[0].strip() or request.remote_addr or "")[:64]


def to_ph(dt):
    """Naive UTC datetime -> naive Philippine time (UTC+8)."""
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt)
        except ValueError:
            return dt
    return dt + PH_OFFSET if isinstance(dt, datetime) else dt


def log_action(action, target_type=None, target_id=None, details=None, username=None, role=None):
    """Write one audit entry. Never raises: auditing must not break a request."""
    try:
        conn = database.get_db()
        conn.execute(
            "INSERT INTO audit_log (username, role, action, target_type, target_id, details, ip_address, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                username if username is not None else session.get("admin"),
                role if role is not None else session.get("role"),
                action,
                target_type,
                None if target_id is None else str(target_id),
                (details or "")[:500] or None,
                client_ip(),
                utcnow(),
            ),
        )
        conn.commit()
        conn.close()
    except Exception as exc:  # pragma: no cover
        print(f"[audit] failed to log {action}: {exc}")


def is_locked_out(username):
    """True if the username had too many failed logins recently."""
    since = utcnow() - timedelta(minutes=LOCKOUT_MINUTES)
    conn = database.get_db()
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM audit_log WHERE action = 'auth.login_failed' "
        "AND LOWER(username) = LOWER(?) AND created_at > ?",
        (username, since),
    ).fetchone()
    conn.close()
    return bool(row and row["n"] >= LOCKOUT_ATTEMPTS)


def init_auth(app):
    """Attach the guard, audit hook, security headers and Jinja filters to the app."""
    production = bool(os.environ.get("VERCEL") or os.environ.get("RENDER"))
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",     # blocks cross-site form posts (CSRF mitigation)
        SESSION_COOKIE_SECURE=production,  # HTTPS-only cookie when deployed
        PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
    )
    app.jinja_env.filters["ph_time"] = lambda dt, fmt="%b %d, %Y %I:%M %p": (
        to_ph(dt).strftime(fmt) if isinstance(to_ph(dt), datetime) else (dt or "")
    )
    app.jinja_env.globals["ROLES"] = ROLES

    @app.before_request
    def admin_guard():
        if not request.path.startswith("/admin"):
            return None
        ep = request.endpoint
        if ep in ("admin_login", "static"):
            return None

        user_id = session.get("user_id")
        if not session.get("admin") or not user_id:
            session.clear()
            return redirect(url_for("admin_login"))

        conn = database.get_db()
        user = conn.execute(
            "SELECT id, username, role, is_active, must_change_password FROM admin_users WHERE id = ?",
            (user_id,),
        ).fetchone()
        conn.close()
        if not user or not user["is_active"]:
            session.clear()
            flash("Your session has ended or the account is disabled.")
            return redirect(url_for("admin_login"))

        session["role"] = user["role"]          # role changes apply immediately
        session["admin"] = user["username"]
        g.admin_user = user

        if user["must_change_password"] and ep not in PASSWORD_CHANGE_ALLOWED:
            flash("Please set a new password before continuing.")
            return redirect(url_for("accounts.account"))

        if user["role"] == ROLE_HRMO or ep is None:
            return None
        if ep in HRMPSB_ALLOWED:
            return None
        log_action("auth.access_denied", details=f"{request.method} {request.path}")
        return render_template("admin/forbidden.html"), 403

    @app.after_request
    def after(resp):
        # Security headers
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        if request.path.startswith("/admin"):
            resp.headers["Cache-Control"] = "no-store"

        # Audit trail for sensitive admin actions
        entry = AUDIT_MAP.get(request.endpoint)
        if entry and request.method in entry[1] and resp.status_code < 400 and session.get("admin"):
            args = request.view_args or {}
            target_type, target_id = ("applicant", args["applicant_id"]) if "applicant_id" in args else ("job", args.get("job_id"))
            log_action(entry[0], target_type, target_id, details=f"job_id={args.get('job_id')}" if "applicant_id" in args else None)
        return resp
