"""Phase 1 — security, RBAC and audit-log tests (integration level, real PostgreSQL)."""
import re

import pytest

import database
from passwords import hash_password, verify_password, is_hashed, password_problem

STRONG = "Str0ngPass99"


def login(client, username, password):
    return client.post("/admin/login", data={"username": username, "password": password})


def q(sql, params=()):
    conn = database.get_db()
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return rows


def run(sql, params=()):
    conn = database.get_db()
    conn.execute(sql, params)
    conn.commit()
    conn.close()


def audit_actions():
    return [r["action"] for r in q("SELECT action FROM audit_log ORDER BY id")]


@pytest.fixture(scope="module")
def admin_client(flask_app):
    """An HRMO administrator who has already completed the forced password change."""
    c = flask_app.test_client()
    r = login(c, "admin", "bulsu2026")
    assert r.status_code == 302 and "/admin/account" in r.headers["Location"]
    r = c.post("/admin/account", data={"current_password": "bulsu2026",
                                       "new_password": STRONG, "confirm_password": STRONG})
    assert r.status_code == 302
    return c


@pytest.fixture(scope="module")
def job_id(admin_client):
    admin_client.post("/admin/job/new", data={
        "campus": "Main Campus - Malolos", "title": "Test Job", "description": "d", "skills": "s",
        "experience": "e", "education": "ed", "other_requirements": "o", "deadline": "2030-01-01"})
    return q("SELECT id FROM job_postings WHERE title = 'Test Job'")[0]["id"]


def create_user(admin_client, username, role):
    r = admin_client.post("/admin/users", data={"username": username, "full_name": username.title(),
                                                 "role": role}, follow_redirects=True)
    m = re.search(r"shown once\): ([A-Za-z0-9]+)", r.get_data(as_text=True))
    assert m, "temporary password was not shown"
    return m.group(1)


# ── Password hashing ─────────────────────────────────────────────────
def test_passwords_are_salted_bcrypt_hashes():
    h1, h2 = hash_password("Abcdef12"), hash_password("Abcdef12")
    assert is_hashed(h1) and h1 != h2            # salted: same input, different hash
    assert verify_password("Abcdef12", h1)
    assert not verify_password("abcdef12", h1)
    assert not verify_password("Abcdef12", "plain-text-not-a-hash")   # legacy plaintext never verifies


@pytest.mark.parametrize("pw,ok", [("short1", False), ("allletters", False), ("12345678", False),
                                   ("bulsu2026", False), ("Valid1234", True)])
def test_password_policy(pw, ok):
    assert (password_problem(pw, "someone") is None) == ok


def test_legacy_plaintext_password_is_migrated_on_startup():
    run("INSERT INTO admin_users (username, password, role) VALUES ('legacy', 'oldpass1', 'hrmo')")
    database.init_db()
    u = q("SELECT password, must_change_password FROM admin_users WHERE username = 'legacy'")[0]
    assert is_hashed(u["password"]) and verify_password("oldpass1", u["password"])
    assert u["must_change_password"] == 0          # only the default password is force-changed


def test_no_plaintext_passwords_remain(admin_client):
    assert all(is_hashed(r["password"]) for r in q("SELECT password FROM admin_users"))


# ── Authentication ───────────────────────────────────────────────────
@pytest.mark.parametrize("path", ["/admin", "/admin/users", "/admin/audit", "/admin/account",
                                  "/admin/job/1/applicants", "/admin/job/1/download/all"])
def test_unauthenticated_admin_pages_redirect_to_login(client, path):
    r = client.get(path)
    assert r.status_code == 302 and "/admin/login" in r.headers["Location"]


def test_failed_login_is_generic_and_audited(client):
    r = login(client, "admin", "wrong-password")
    assert r.status_code == 200 and "Invalid username or password" in r.get_data(as_text=True)
    r2 = login(client, "no-such-user", "whatever1")          # same message: no username enumeration
    assert r2.status_code == 200 and "Invalid username or password" in r2.get_data(as_text=True)
    assert audit_actions().count("auth.login_failed") >= 2


def test_default_password_forces_change_and_blocks_other_pages(flask_app):
    run("INSERT INTO admin_users (username, password, role, must_change_password) VALUES ('forced', %s, 'hrmo', 1)"
        .replace("%s", "'" + hash_password("Temp12345") + "'"))
    c = flask_app.test_client()
    r = login(c, "forced", "Temp12345")
    assert "/admin/account" in r.headers["Location"]
    assert "/admin/account" in c.get("/admin").headers["Location"]           # blocked until changed
    bad = c.post("/admin/account", data={"current_password": "Temp12345", "new_password": "short",
                                         "confirm_password": "short"})
    assert bad.status_code == 200                                              # rejected, stays on page
    assert "/admin/account" in c.get("/admin").headers["Location"]
    ok = c.post("/admin/account", data={"current_password": "Temp12345", "new_password": STRONG + "x",
                                        "confirm_password": STRONG + "x"})
    assert ok.status_code == 302
    assert c.get("/admin").status_code == 200


def test_lockout_after_repeated_failures(flask_app):
    run("INSERT INTO admin_users (username, password, role) VALUES ('lockme', %s, 'hrmo')"
        .replace("%s", "'" + hash_password("RealPass123") + "'"))
    c = flask_app.test_client()
    for _ in range(5):
        login(c, "lockme", "bad-guess")
    r = login(c, "lockme", "RealPass123")          # correct password, but locked out
    assert r.status_code == 200 and "Too many failed attempts" in r.get_data(as_text=True)
    assert "auth.login_blocked" in audit_actions()
    # an unrelated account is not affected
    assert login(flask_app.test_client(), "admin", STRONG).status_code == 302


def test_session_cookie_flags_and_security_headers(flask_app):
    c = flask_app.test_client()
    r = login(c, "admin", STRONG)
    cookie = r.headers.get("Set-Cookie", "")
    assert "HttpOnly" in cookie and "SameSite=Lax" in cookie
    page = c.get("/admin")
    assert page.headers["X-Content-Type-Options"] == "nosniff"
    assert page.headers["X-Frame-Options"] == "SAMEORIGIN"
    assert page.headers["Cache-Control"] == "no-store"


def test_logout_ends_session(flask_app):
    c = flask_app.test_client()
    login(c, "admin", STRONG)
    assert c.get("/admin").status_code == 200
    c.get("/admin/logout")
    assert c.get("/admin").status_code == 302


# ── Role-based access control ────────────────────────────────────────
@pytest.fixture(scope="module")
def hrmpsb_client(admin_client, flask_app, job_id):
    temp = create_user(admin_client, "boardmember", "hrmpsb")
    c = flask_app.test_client()
    assert "/admin/account" in login(c, "boardmember", temp).headers["Location"]
    c.post("/admin/account", data={"current_password": temp, "new_password": STRONG,
                                   "confirm_password": STRONG})
    return c


def test_hrmpsb_can_review_applicants(hrmpsb_client, job_id):
    assert hrmpsb_client.get("/admin").status_code == 200
    assert hrmpsb_client.get(f"/admin/job/{job_id}/applicants").status_code == 200
    assert hrmpsb_client.get(f"/admin/job/{job_id}/download/qualified").status_code in (200, 302)


@pytest.mark.parametrize("method,path", [
    ("GET", "/admin/job/new"), ("POST", "/admin/job/new"), ("GET", "/admin/job/{job}/edit"),
    ("POST", "/admin/job/{job}/delete"), ("POST", "/admin/job/{job}/restore"),
    ("GET", "/admin/job/{job}/screen"), ("GET", "/admin/job/{job}/rescreen-all"),
    ("GET", "/admin/job/{job}/generate-ad"), ("GET", "/admin/users"), ("POST", "/admin/users"),
    ("GET", "/admin/audit"), ("POST", "/admin/users/1/reset-password"), ("POST", "/admin/users/1/toggle"),
])
def test_hrmpsb_is_blocked_from_admin_functions(hrmpsb_client, job_id, method, path):
    r = hrmpsb_client.open(path.format(job=job_id), method=method)
    assert r.status_code == 403


def test_hrmpsb_ui_hides_admin_controls(hrmpsb_client):
    html = hrmpsb_client.get("/admin").get_data(as_text=True)
    assert "Add New Job Posting" not in html and "/admin/users" not in html and "/admin/audit" not in html
    assert "HRMPSB" in html


def test_hrmo_ui_shows_admin_controls(admin_client):
    html = admin_client.get("/admin").get_data(as_text=True)
    assert "Add New Job Posting" in html and "/admin/users" in html and "/admin/audit" in html


def test_denied_attempts_are_audited(hrmpsb_client):
    assert "auth.access_denied" in audit_actions()


# ── Account management safeguards ────────────────────────────────────
def test_deactivated_user_loses_access_immediately(admin_client, hrmpsb_client):
    assert hrmpsb_client.get("/admin").status_code == 200
    uid = q("SELECT id FROM admin_users WHERE username = 'boardmember'")[0]["id"]
    admin_client.post(f"/admin/users/{uid}/toggle")
    r = hrmpsb_client.get("/admin")
    assert r.status_code == 302 and "/admin/login" in r.headers["Location"]
    assert login(hrmpsb_client, "boardmember", STRONG).status_code == 200      # cannot sign in either
    admin_client.post(f"/admin/users/{uid}/toggle")                            # re-enable


def test_cannot_deactivate_self_or_remove_last_hrmo(admin_client):
    # leave exactly one active HRMO (admin): deactivate the other test HRMOs directly
    run("UPDATE admin_users SET is_active = 0 WHERE role = 'hrmo' AND username <> 'admin'")
    me = q("SELECT id FROM admin_users WHERE username = 'admin'")[0]["id"]
    admin_client.post(f"/admin/users/{me}/toggle")
    admin_client.post(f"/admin/users/{me}/role", data={"role": "hrmpsb"})
    row = q("SELECT role, is_active FROM admin_users WHERE id = ?", (me,))[0]
    assert row["role"] == "hrmo" and row["is_active"] == 1


def test_duplicate_and_invalid_usernames_rejected(admin_client):
    before = q("SELECT COUNT(*) AS n FROM admin_users")[0]["n"]
    admin_client.post("/admin/users", data={"username": "admin", "full_name": "X", "role": "hrmo"})
    admin_client.post("/admin/users", data={"username": "Bad Name!", "full_name": "X", "role": "hrmo"})
    admin_client.post("/admin/users", data={"username": "okname", "full_name": "X", "role": "superuser"})
    assert q("SELECT COUNT(*) AS n FROM admin_users")[0]["n"] == before


def test_reset_password_forces_change(admin_client, flask_app):
    temp = create_user(admin_client, "resetme", "hrmo")
    uid = q("SELECT id FROM admin_users WHERE username = 'resetme'")[0]["id"]
    r = admin_client.post(f"/admin/users/{uid}/reset-password", follow_redirects=True)
    new_temp = re.search(r"shown once\): ([A-Za-z0-9]+)", r.get_data(as_text=True)).group(1)
    assert new_temp != temp
    c = flask_app.test_client()
    assert login(c, "resetme", temp).status_code == 200                       # old temp no longer works
    assert "/admin/account" in login(c, "resetme", new_temp).headers["Location"]


# ── Audit trail ──────────────────────────────────────────────────────
def test_audit_log_records_key_events(admin_client, job_id):
    admin_client.get(f"/admin/job/{job_id}/applicants")
    actions = set(audit_actions())
    for expected in ("auth.login", "auth.login_failed", "auth.password_changed", "user.create",
                     "job.create", "applicants.view", "user.password_reset"):
        assert expected in actions, f"missing audit action: {expected}"
    row = q("SELECT * FROM audit_log WHERE action = 'job.create' LIMIT 1")[0]
    assert row["username"] == "admin" and row["role"] == "hrmo" and row["created_at"] is not None


def test_audit_page_filters(admin_client):
    html = admin_client.get("/admin/audit?q=user.create").get_data(as_text=True)
    assert "user.create" in html and "auth.logout" not in html


# ── Public side unaffected ───────────────────────────────────────────
def test_public_pages_still_work(client, job_id):
    assert client.get("/").status_code == 200
    assert client.get(f"/job/{job_id}").status_code == 200
    assert client.get("/admin/login").status_code == 200
