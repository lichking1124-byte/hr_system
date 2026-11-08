from flask import Flask, render_template, request, send_file, redirect, url_for, session, flash, Response, jsonify
import os
import re
import json
import time
import hashlib
import requests
from io import BytesIO
from urllib.parse import urlparse
from datetime import datetime, timedelta, date
from database import get_db, init_db
from resume_parser import extract_text_from_pdf
from screening_rules import decide, posting_terms, applicant_profile
from duplicates import PeopleIndex, same_email
from ai_screener import advise_applicant, read_documents
from exporter import export_qualified, export_all, export_applicant_list, export_applicants_by_position
from ad_generator import generate_job_ad_image
from cloudinary_uploader import upload_file
from config import SECRET_KEY, CAMPUSES
from auth import init_auth, log_action, is_locked_out, utcnow, LOCKOUT_MINUTES
from accounts import accounts
from passwords import verify_password
from doc_presets import PRESETS, DOC_HINTS, VALID_TYPES, DEFAULT_DOCS, LIST_KEYS

app = Flask(__name__)
app.secret_key = SECRET_KEY

try:
    os.makedirs("static/uploads", exist_ok=True)
    os.makedirs("static/ads", exist_ok=True)
except OSError:
    pass

init_db()


def _add_missing_columns(table, columns):
    """Adds any of `columns` (all TEXT) that the table does not have yet.

    It first LOOKS at which columns exist (a plain read that blocks nobody) and only runs ALTER TABLE
    for the ones that are missing. ALTER TABLE takes Postgres's strictest lock even when the column
    is already there, and running it on every start of the site made it collide with visitors'
    requests (a "deadlock detected" error). Now a normal start runs no ALTER at all."""
    conn = get_db()
    try:
        have = {r['column_name'] for r in conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = ?", (table,)).fetchall()}
        for column in columns:
            if column in have:
                continue
            try:
                conn.execute("SET LOCAL lock_timeout = '5s'")   # never wait long (applies to this step only)
                conn.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} TEXT")
                conn.commit()
            except Exception as e:
                try:
                    conn.rollback()
                except Exception:
                    pass
                print(f"adding {table}.{column} failed (it will be retried on the next start): {e}")
    except Exception as e:
        print(f"column check for {table} failed: {e}")
    finally:
        conn.close()


def ensure_position_type_column():
    """job_postings.position_type (academic / non_academic)."""
    _add_missing_columns('job_postings', ['position_type'])


ensure_position_type_column()


def ensure_extra_field_columns():
    """HR-defined extra questions on a posting, and the applicants' answers to them."""
    _add_missing_columns('job_postings', ['extra_fields', 'training', 'custom_qualifications'])
    _add_missing_columns('applicants', ['extra_answers'])


ensure_extra_field_columns()


# ---------------------------------------------------------------- shared document lists
def _fallback_document_lists():
    return {'custom': list(DEFAULT_DOCS), 'academic': list(PRESETS['academic']),
            'non_academic': list(PRESETS['non_academic'])}


def seed_document_lists():
    """First start only: copy the starting lists from doc_presets.py into the database.
    After that the database is the truth, so HR's adds and deletes stick."""
    conn = get_db()
    try:
        if conn.execute('SELECT COUNT(*) AS n FROM document_lists').fetchone()['n']:
            return
        for key, names in _fallback_document_lists().items():
            for i, name in enumerate(names, start=1):
                conn.execute('INSERT INTO document_lists (list_key, name, sort_order) VALUES (?, ?, ?) '
                             'ON CONFLICT DO NOTHING', (key, name, i))
        conn.commit()
    except Exception as e:
        print(f"Seeding the document lists failed: {e}")
    finally:
        conn.close()


seed_document_lists()


def load_document_lists(conn=None):
    """{'custom': [...], 'academic': [...], 'non_academic': [...]} in the order HR set.
    Falls back to the starting lists if the table is not available, so the form never breaks."""
    own = conn is None
    conn = conn or get_db()
    out = {k: [] for k in LIST_KEYS}
    try:
        for r in conn.execute('SELECT list_key, name FROM document_lists ORDER BY sort_order, id').fetchall():
            if r['list_key'] in out:
                out[r['list_key']].append(r['name'])
    except Exception as e:
        print(f"Reading the document lists failed, using the starting lists: {e}")
        return _fallback_document_lists()
    finally:
        if own:
            conn.close()
    fallback = _fallback_document_lists()
    return {k: (out[k] or fallback[k]) for k in LIST_KEYS}


# ---------------------------------------------------------------- extra qualifications (per posting)
def parse_custom_qualifications(form):
    """Qualifications HR added on the posting form -> [{'label', 'value'}]. Posted as two parallel lists."""
    labels = form.getlist('cq_label')
    values = form.getlist('cq_value')
    out, seen = [], set()
    for i, raw in enumerate(labels):
        label = re.sub(r'\s+', ' ', (raw or '').strip())[:60]
        value = re.sub(r'\s+', ' ', (values[i] if i < len(values) else '').strip())[:500]
        if not label or not value or label.lower() in seen:
            continue
        seen.add(label.lower())
        out.append({'label': label, 'value': value})
        if len(out) >= 10:
            break
    return out


def load_custom_qualifications(job):
    try:
        value = json.loads(job['custom_qualifications']) if job and job['custom_qualifications'] else []
        return [q for q in value if isinstance(q, dict) and q.get('label') and q.get('value')]
    except (ValueError, TypeError, KeyError):
        return []


DOC_NAME_OK = re.compile(r"^\w[\w &()/,.'\-]{1,119}$")


def parse_extra_fields(form):
    """HR's extra applicant questions from the posting form -> [{'label', 'required'}]."""
    labels = form.getlist('extra_label')
    needs = form.getlist('extra_required')
    out, seen = [], set()
    for i, raw in enumerate(labels):
        label = re.sub(r'\s+', ' ', (raw or '').strip())[:80]
        if not label or label.lower() in seen:
            continue
        seen.add(label.lower())
        out.append({'label': label, 'required': (needs[i] if i < len(needs) else '0') == '1'})
        if len(out) >= 10:
            break
    return out


def load_extra_fields(job):
    try:
        value = json.loads(job['extra_fields']) if job and job['extra_fields'] else []
        return [f for f in value if isinstance(f, dict) and f.get('label')]
    except (ValueError, TypeError, KeyError):
        return []


def ensure_screening_columns():
    """The columns for the saved AI reading, HR's decision and replaced documents."""
    _add_missing_columns('applicants', ['ai_reading', 'ai_reading_hash', 'system_result', 'hr_decision',
                                        'hr_decision_reason', 'hr_decision_by', 'hr_decision_at',
                                        'education_found', 'experience_months'])
    _add_missing_columns('applicant_documents', ['replaced_at'])


ensure_screening_columns()


def clean_position_type(value):
    return value if value in VALID_TYPES else None


init_auth(app)
app.register_blueprint(accounts)


@app.template_filter('from_json')
def from_json_filter(value):
    """Turn a stored JSON list (text) back into a Python list for templates."""
    if not value:
        return []
    if isinstance(value, list):
        return value
    try:
        data = json.loads(value)
        return data if isinstance(data, list) else []
    except (ValueError, TypeError):
        return []

# ─── APPLICANT ROUTES ────────────────────────────────────────────

@app.route('/')
def home():
    conn = get_db()
    postings = conn.execute(
        'SELECT * FROM job_postings WHERE is_active = 1 AND archived_at IS NULL ORDER BY campus, title'
    ).fetchall()
    conn.close()

    jobs_by_campus = {}
    for campus in CAMPUSES:
        jobs_by_campus[campus] = [p for p in postings if p['campus'] == campus]

    return render_template('applicant/home.html', jobs_by_campus=jobs_by_campus)

@app.route('/job/<int:job_id>')
def job_detail(job_id):
    conn = get_db()
    job = conn.execute(
        'SELECT * FROM job_postings WHERE id = ? AND is_active = 1 AND archived_at IS NULL', (job_id,)
    ).fetchone()
    conn.close()

    if not job:
        return "Job not found", 404

    required_docs = json.loads(job['required_documents']) if job['required_documents'] else []
    return render_template('applicant/job_detail.html', job=job, required_docs=required_docs,
                           custom_quals=load_custom_qualifications(job))

@app.route('/apply/<int:job_id>', methods=['GET', 'POST'])
def apply(job_id):
    conn = get_db()
    job = conn.execute(
        'SELECT * FROM job_postings WHERE id = ? AND is_active = 1 AND archived_at IS NULL', (job_id,)
    ).fetchone()
    conn.close()

    if not job:
        return "Job not found", 404

    required_docs = json.loads(job['required_documents']) if job['required_documents'] else []
    extra_fields = load_extra_fields(job)

    if request.method == 'POST':
        extra_answers = []
        for i, f in enumerate(extra_fields):
            answer = (request.form.get(f'extra_{i}') or '').strip()[:300]
            if f.get('required') and not answer:
                return (f'"{f["label"]}" is required. Go back and try again.', 400)
            extra_answers.append({'label': f['label'], 'answer': answer})

        allowed_types = ('.pdf', '.docx')
        for doc_type in required_docs:
            field_name = doc_type.replace(' ', '_').lower()
            files = [f for f in request.files.getlist(field_name) if f and f.filename]
            if not files and not doc_type.endswith('(if applicable)'):
                return (f'"{doc_type}" is required. Incomplete applications cannot be submitted. '
                        f'Go back and try again.', 400)
            for file in files:
                if os.path.splitext(file.filename)[1].lower() not in allowed_types:
                    return (f'"{file.filename}" is not accepted. Please upload PDF or Word (.docx) '
                            f'files only. Go back and try again.', 400)

        if not request.form.get('consent'):
            return ('Please tick the data privacy consent box to submit your application. '
                    'Go back and try again.', 400)

        full_name = request.form['full_name']
        email = request.form['email']
        phone = request.form['phone']

        # The same email cannot apply to the SAME position twice. Applying to other positions is allowed.
        conn = get_db()
        earlier = conn.execute('SELECT email FROM applicants WHERE job_id = ?', (job_id,)).fetchall()
        conn.close()
        if any(same_email(email, r['email']) for r in earlier):
            log_action('application.duplicate_blocked', 'job', job_id,
                       'the same email had already applied to this position', username='(applicant)', role='applicant')
            return render_template('applicant/already_applied.html', job=job), 409

        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO applicants (job_id, full_name, email, phone, extra_answers)
            VALUES (?, ?, ?, ?, ?)
        ''', (job_id, full_name, email, phone, json.dumps(extra_answers) if extra_answers else None))
        applicant_id = cursor.lastrowid

        # Control number: unique, human-readable reference the applicant can
        # quote when following up (and later, for status lookups).
        control_number = f"BULSU-{datetime.now().year}-{applicant_id:05d}"
        cursor.execute(
            'UPDATE applicants SET control_number = ? WHERE id = ?',
            (control_number, applicant_id)
        )

        for doc_type in required_docs:
            field_name = doc_type.replace(' ', '_').lower()
            files = request.files.getlist(field_name)
            for file in files:
                if file and file.filename:
                    filename = f"{field_name}_{file.filename}"
                    cloud_url = upload_file(file, folder=f"applicant_{applicant_id}")
                    cursor.execute('''
                        INSERT INTO applicant_documents
                        (applicant_id, document_type, filename, filepath)
                        VALUES (?, ?, ?, ?)
                    ''', (applicant_id, doc_type, filename, cloud_url))

        conn.commit()
        conn.close()
        log_action('application.submit', 'applicant', applicant_id,
                   f'job_id={job_id}; data privacy consent given', username='(applicant)', role='applicant')

        return render_template('applicant/success.html', name=full_name, job=job,
                               control_number=control_number)

    return render_template('applicant/apply.html', job=job, required_docs=required_docs,
                           hints=DOC_HINTS, extra_fields=extra_fields)

@app.route('/status', methods=['GET', 'POST'])
def check_status():
    result = None
    searched = False

    if request.method == 'POST':
        searched = True
        control_number = request.form.get('control_number', '').strip().upper()
        email = request.form.get('email', '').strip().lower()

        if control_number and email:
            conn = get_db()
            row = conn.execute('''
                SELECT a.control_number, a.applied_at, a.is_screened, a.full_name,
                       a.screening_result, a.hr_decision,
                       j.title, j.campus
                FROM applicants a
                JOIN job_postings j ON j.id = a.job_id
                WHERE a.control_number = ? AND LOWER(a.email) = ?
            ''', (control_number, email)).fetchone()
            conn.close()

            if row:
                result = {
                    'control_number': row['control_number'],
                    'first_name': (row['full_name'] or '').split(' ')[0],
                    'position': row['title'],
                    'campus': row['campus'],
                    'applied_date': str(row['applied_at'])[:10],
                    'screened': bool(row['is_screened']),
                    # the status page shows the "replace a document" link only while that is still allowed
                    'can_replace': documents_can_be_replaced(row),
                }

    return render_template('applicant/status.html', result=result, searched=searched)

MAX_REPLACEMENT_FILES = 5


def documents_can_be_replaced(applicant):
    """The rule for when an applicant may still correct a document.
    Allowed: before HR has screened the application, or while it is Under review.
    Locked: once the application is Qualified or Disqualified, or HR has decided it.
    (Change this one function if HR wants a different window.)"""
    if _col(applicant, 'hr_decision'):
        return False
    if not applicant['is_screened']:
        return True
    return applicant['screening_result'] == 'under_review'


def _display_filename(stored, doc_type):
    """Stored names look like 'resume_myfile.pdf'. Show the applicant's own file name."""
    prefix = doc_type.replace(' ', '_').lower() + '_'
    return stored[len(prefix):] if stored.startswith(prefix) else stored


@app.route('/status/documents', methods=['GET', 'POST'])
def applicant_documents_page():
    """An applicant (control number + email) can swap a wrongly uploaded file for the right one."""
    ctx = dict(applicant=None, slots=[], allowed=False, error=None, message=None,
               control_number='', email='')
    if request.method != 'POST':
        return render_template('applicant/documents.html', **ctx)

    control_number = request.form.get('control_number', '').strip().upper()
    email = request.form.get('email', '').strip().lower()
    ctx.update(control_number=control_number, email=email)

    conn = get_db()
    applicant = None
    if control_number and email:
        applicant = conn.execute('''
            SELECT a.*, j.title AS job_title, j.campus AS job_campus, j.required_documents AS job_required_documents
            FROM applicants a JOIN job_postings j ON j.id = a.job_id
            WHERE a.control_number = ? AND LOWER(a.email) = ?
        ''', (control_number, email)).fetchone()
    if not applicant:
        conn.close()
        ctx['error'] = 'We could not find an application with that control number and email.'
        return render_template('applicant/documents.html', **ctx)

    required_docs = json.loads(applicant['job_required_documents']) if applicant['job_required_documents'] else []
    allowed = documents_can_be_replaced(applicant)

    if request.form.get('action') == 'replace':
        slot = request.form.get('slot', '')
        files = [f for f in request.files.getlist('file') if f and f.filename][:MAX_REPLACEMENT_FILES]
        if not allowed:
            ctx['error'] = ('Your documents can no longer be changed here because your application has already '
                            'been processed. Please contact HR if something needs correcting.')
        elif slot not in required_docs:
            ctx['error'] = 'That document type is not part of this application.'
        elif not files:
            ctx['error'] = 'Please choose the file you want to upload.'
        elif any(os.path.splitext(f.filename)[1].lower() not in ('.pdf', '.docx') for f in files):
            ctx['error'] = 'Please upload PDF or Word (.docx) files only.'
        else:
            try:
                field_name = slot.replace(' ', '_').lower()
                uploaded = [(f"{field_name}_{f.filename}", upload_file(f, folder=f"applicant_{applicant['id']}"))
                            for f in files]          # upload first: if it fails, nothing has changed
                now = datetime.utcnow().isoformat(timespec='seconds')
                conn.execute('UPDATE applicant_documents SET replaced_at = ? '
                             'WHERE applicant_id = ? AND document_type = ? AND replaced_at IS NULL',
                             (now, applicant['id'], slot))
                for filename, cloud_url in uploaded:
                    conn.execute('INSERT INTO applicant_documents (applicant_id, document_type, filename, filepath) '
                                 'VALUES (?, ?, ?, ?)', (applicant['id'], slot, filename, cloud_url))
                if applicant['is_screened']:
                    # the old result was about the old files: HR must screen this applicant again
                    conn.execute('UPDATE applicants SET is_screened = 0 WHERE id = ?', (applicant['id'],))
                conn.commit()
                log_action('application.document_replaced', 'applicant', applicant['id'],
                           f'slot={slot}; files={len(uploaded)}', username='(applicant)', role='applicant')
                ctx['message'] = (f'Your "{slot}" file was replaced. HR will check your application again.')
            except Exception as e:
                print(f"Document replacement failed for applicant {applicant['id']}: {e}")
                ctx['error'] = 'Your file could not be uploaded. Nothing was changed. Please try again.'
        # the rules may have changed because of this replacement
        applicant = conn.execute('''
            SELECT a.*, j.title AS job_title, j.campus AS job_campus, j.required_documents AS job_required_documents
            FROM applicants a JOIN job_postings j ON j.id = a.job_id WHERE a.id = ?
        ''', (applicant['id'],)).fetchone()
        allowed = documents_can_be_replaced(applicant)

    current = _applicant_documents(conn, applicant['id'])
    conn.close()
    slots = [{'type': t,
              'optional': t.endswith('(if applicable)'),
              'files': [_display_filename(d['filename'], t) for d in current if d['document_type'] == t]}
             for t in required_docs]
    ctx.update(applicant=applicant, slots=slots, allowed=allowed)
    return render_template('applicant/documents.html', **ctx)


# ─── ADMIN ROUTES ────────────────────────────────────────────────

@app.route('/admin')
def admin_home():
    if 'admin' not in session:
        return redirect(url_for('admin_login'))

    conn = get_db()
    active_postings = conn.execute(
        'SELECT * FROM job_postings WHERE archived_at IS NULL ORDER BY campus, title'
    ).fetchall()
    archived_postings = conn.execute(
        'SELECT * FROM job_postings WHERE archived_at IS NOT NULL ORDER BY archived_at DESC'
    ).fetchall()
    conn.close()

    active_by_campus = {}
    for campus in CAMPUSES:
        active_by_campus[campus] = [p for p in active_postings if p['campus'] == campus]

    return render_template('admin/dashboard.html',
                           jobs_by_campus=active_by_campus,
                           archived_jobs=archived_postings,
                           campuses=CAMPUSES)

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')

        if username and is_locked_out(username):
            log_action('auth.login_blocked', username=username[:50], role='')
            flash(f'Too many failed attempts. Please try again in {LOCKOUT_MINUTES} minutes.')
            return render_template('admin/login.html')

        conn = get_db()
        user = conn.execute(
            'SELECT * FROM admin_users WHERE LOWER(username) = LOWER(?)', (username,)
        ).fetchone()
        ok = verify_password(password, user['password'] if user else None)

        if user and ok and user['is_active']:
            conn.execute('UPDATE admin_users SET last_login = ? WHERE id = ?',
                         (utcnow(), user['id']))
            conn.commit()
            conn.close()
            session.clear()
            session.permanent = True
            session['admin'] = user['username']
            session['user_id'] = user['id']
            session['role'] = user['role']
            log_action('auth.login', 'user', user['username'])
            if user['must_change_password']:
                flash('Please set a new password to continue.')
                return redirect(url_for('accounts.account'))
            return redirect(url_for('admin_home'))

        conn.close()
        log_action('auth.login_failed', username=username[:50], role='')
        flash('Invalid username or password')

    return render_template('admin/login.html')

@app.route('/admin/logout')
def admin_logout():
    log_action('auth.logout')
    session.clear()
    return redirect(url_for('admin_login'))

@app.route('/admin/job/new', methods=['GET', 'POST'])
def admin_new_job():
    if 'admin' not in session:
        return redirect(url_for('admin_login'))

    if request.method == 'POST':
        required_docs = request.form.getlist('required_documents')

        mandatory = []
        if 'mandatory_skills' in request.form: mandatory.append('skills')
        if 'mandatory_experience' in request.form: mandatory.append('experience')
        if 'mandatory_education' in request.form: mandatory.append('education')
        if 'mandatory_other' in request.form: mandatory.append('other')

        conn = get_db()
        conn.execute('''
            INSERT INTO job_postings
            (campus, title, description, skills, experience,
             education, other_requirements, required_documents, mandatory_requirements,
             deadline, position_type, extra_fields, training, custom_qualifications)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            request.form['campus'],
            request.form['title'],
            request.form['description'],
            request.form['skills'],
            request.form['experience'],
            request.form['education'],
            request.form['other_requirements'],
            json.dumps(required_docs),
            json.dumps(mandatory),
            request.form.get('deadline') or None,
            clean_position_type(request.form.get('position_type')),
            json.dumps(parse_extra_fields(request.form)),
            (request.form.get('training') or '').strip()[:500],
            json.dumps(parse_custom_qualifications(request.form))
        ))
        conn.commit()
        conn.close()
        flash('Job posting created successfully!')
        return redirect(url_for('admin_home'))

    lists = load_document_lists()
    return render_template('admin/job_form.html', job=None, campuses=CAMPUSES, mandatory=[],
                           presets={'academic': lists['academic'], 'non_academic': lists['non_academic']},
                           default_docs=lists['custom'], extra_fields=[], custom_quals=[])

@app.route('/admin/job/<int:job_id>/edit', methods=['GET', 'POST'])
def admin_edit_job(job_id):
    if 'admin' not in session:
        return redirect(url_for('admin_login'))

    conn = get_db()
    job = conn.execute('SELECT * FROM job_postings WHERE id = ?', (job_id,)).fetchone()
    conn.close()

    if not job:
        return "Job not found", 404

    if request.method == 'POST':
        required_docs = request.form.getlist('required_documents')

        mandatory = []
        if 'mandatory_skills' in request.form: mandatory.append('skills')
        if 'mandatory_experience' in request.form: mandatory.append('experience')
        if 'mandatory_education' in request.form: mandatory.append('education')
        if 'mandatory_other' in request.form: mandatory.append('other')

        conn = get_db()
        conn.execute('''
            UPDATE job_postings SET
            campus=?, title=?, description=?, skills=?,
            experience=?, education=?, other_requirements=?,
            required_documents=?, mandatory_requirements=?, is_active=?,
            deadline=?, position_type=?, extra_fields=?, training=?, custom_qualifications=?
            WHERE id=?
        ''', (
            request.form['campus'],
            request.form['title'],
            request.form['description'],
            request.form['skills'],
            request.form['experience'],
            request.form['education'],
            request.form['other_requirements'],
            json.dumps(required_docs),
            json.dumps(mandatory),
            1 if 'is_active' in request.form else 0,
            request.form.get('deadline') or None,
            clean_position_type(request.form.get('position_type')),
            json.dumps(parse_extra_fields(request.form)),
            (request.form.get('training') or '').strip()[:500],
            json.dumps(parse_custom_qualifications(request.form)),
            job_id
        ))
        conn.commit()
        conn.close()
        flash('Job posting updated successfully!')
        return redirect(url_for('admin_home'))

    required_docs = json.loads(job['required_documents']) if job['required_documents'] else []
    mandatory = json.loads(job['mandatory_requirements']) if job['mandatory_requirements'] else []
    lists = load_document_lists()
    return render_template('admin/job_form.html', job=job, required_docs=required_docs,
                           mandatory=mandatory, campuses=CAMPUSES,
                           presets={'academic': lists['academic'], 'non_academic': lists['non_academic']},
                           default_docs=lists['custom'], extra_fields=load_extra_fields(job),
                           custom_quals=load_custom_qualifications(job))

@app.route('/admin/document-types/add', methods=['POST'])
def admin_document_type_add():
    """Add a document to the shared lists, so it is available in every job posting."""
    if 'admin' not in session:
        return jsonify(ok=False, error='Please log in again.'), 401
    data = request.get_json(silent=True) or {}
    name = re.sub(r'\s+', ' ', str(data.get('name', '')).strip())
    if not DOC_NAME_OK.match(name):
        return jsonify(ok=False, error='Use 2 to 120 characters: letters, numbers, spaces and & ( ) / , . - only.'), 400

    conn = get_db()
    lists = load_document_lists(conn)
    existing = next((n for names in lists.values() for n in names if n.lower() == name.lower()), None)
    canonical = existing or name
    added_to = 0
    for key in LIST_KEYS:
        if canonical.lower() in [n.lower() for n in lists[key]]:
            continue
        conn.execute('INSERT INTO document_lists (list_key, name, sort_order) VALUES '
                     '(?, ?, (SELECT COALESCE(MAX(sort_order), 0) + 1 FROM document_lists WHERE list_key = ?)) '
                     'ON CONFLICT DO NOTHING', (key, canonical, key))
        added_to += 1
    conn.commit()
    conn.close()
    if added_to:
        log_action('document_type.add', 'document_type', canonical, f'added to {added_to} list(s)')
    return jsonify(ok=True, name=canonical, already_existed=not added_to)


@app.route('/admin/document-types/delete', methods=['POST'])
def admin_document_type_delete():
    """Delete a document from the shared lists and from every job posting that requires it.
    Files applicants already uploaded stay on their records."""
    if 'admin' not in session:
        return jsonify(ok=False, error='Please log in again.'), 401
    data = request.get_json(silent=True) or {}
    name = re.sub(r'\s+', ' ', str(data.get('name', '')).strip())
    if not name:
        return jsonify(ok=False, error='No document was given.'), 400

    conn = get_db()
    lists = load_document_lists(conn)
    for key, names in lists.items():
        if [n for n in names if n.lower() != name.lower()] == [] and names:
            conn.close()
            return jsonify(ok=False, error='At least one document must stay in each list.'), 400

    conn.execute('DELETE FROM document_lists WHERE LOWER(name) = LOWER(?)', (name,))
    updated = 0
    for j in conn.execute('SELECT id, required_documents FROM job_postings').fetchall():
        try:
            docs = json.loads(j['required_documents']) if j['required_documents'] else []
        except (ValueError, TypeError):
            continue
        kept = [d for d in docs if str(d).lower() != name.lower()]
        if len(kept) != len(docs):
            conn.execute('UPDATE job_postings SET required_documents = ? WHERE id = ?',
                         (json.dumps(kept), j['id']))
            updated += 1
    conn.commit()
    conn.close()
    log_action('document_type.delete', 'document_type', name, f'removed from the lists and from {updated} posting(s)')
    return jsonify(ok=True, name=name, postings_updated=updated)


@app.route('/admin/job/<int:job_id>/delete', methods=['POST'])
def admin_delete_job(job_id):
    if 'admin' not in session:
        return redirect(url_for('admin_login'))

    conn = get_db()
    job = conn.execute('SELECT * FROM job_postings WHERE id = ?', (job_id,)).fetchone()

    if not job:
        conn.close()
        return "Job not found", 404

    if job['archived_at']:
        archived_date = datetime.fromisoformat(str(job['archived_at']))
        six_months_ago = datetime.now() - timedelta(days=180)

        if archived_date > six_months_ago:
            flash('Job posting cannot be permanently deleted until 6 months after archiving.')
            conn.close()
            return redirect(url_for('admin_home'))
        else:
            conn.execute('DELETE FROM job_postings WHERE id = ?', (job_id,))
            conn.commit()
            conn.close()
            flash('Job posting permanently deleted.')
            return redirect(url_for('admin_home'))
    else:
        conn.execute(
            'UPDATE job_postings SET archived_at = ? WHERE id = ?',
            (datetime.now().isoformat(), job_id)
        )
        conn.commit()
        conn.close()
        flash('Job posting archived.')
        return redirect(url_for('admin_home'))

@app.route('/admin/job/<int:job_id>/restore', methods=['POST'])
def admin_restore_job(job_id):
    if 'admin' not in session:
        return redirect(url_for('admin_login'))

    conn = get_db()
    conn.execute('UPDATE job_postings SET archived_at = NULL WHERE id = ?', (job_id,))
    conn.commit()
    conn.close()
    flash('Job posting restored.')
    return redirect(url_for('admin_home'))

@app.route('/admin/job/<int:job_id>/applicants')
def admin_applicants(job_id):
    if 'admin' not in session:
        return redirect(url_for('admin_login'))

    conn = get_db()
    job = conn.execute('SELECT * FROM job_postings WHERE id = ?', (job_id,)).fetchone()
    applicants = conn.execute(
        'SELECT * FROM applicants WHERE job_id = ? ORDER BY applied_at DESC', (job_id,)
    ).fetchall()

    # Which applicants look like the same person as someone else (information for HR only)
    related_map = {}
    if applicants:
        people = PeopleIndex(_all_applications(conn))
        for a in applicants:
            links = people.related(dict(a))
            if links:
                same = sum(1 for m in links if m['same_position'])
                related_map[a['id']] = {'same_position': same, 'other_positions': len(links) - same}
    conn.close()

    return render_template('admin/applicants.html', job=job, applicants=applicants, related_map=related_map)

STATUS_LABELS = {'qualified': 'Qualified', 'under_review': 'Under review', 'unqualified': 'Disqualified'}


def _all_applications(conn):
    """Every application, with its position, for matching people across positions."""
    rows = conn.execute('''
        SELECT a.id, a.job_id, a.full_name, a.email, a.phone, a.control_number, a.applied_at,
               a.is_screened, a.screening_result, j.title AS job_title, j.campus AS job_campus
        FROM applicants a JOIN job_postings j ON j.id = a.job_id
    ''').fetchall()
    return [dict(r) for r in rows]


def _status_label(row):
    if not row.get('is_screened'):
        return 'Not screened yet'
    return STATUS_LABELS.get(row.get('screening_result'), 'Not screened yet')


DOC_MIMETYPES = {
    '.pdf': 'application/pdf',
    '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
    '.gif': 'image/gif', '.webp': 'image/webp',
}
IMAGE_EXTS = {'.png', '.jpg', '.jpeg', '.gif', '.webp'}


def _doc_kind(ext):
    if ext in IMAGE_EXTS:
        return 'image'
    if ext in ('.pdf', '.docx'):
        return 'frame'
    return 'none'


def _doc_ext(doc):
    name = (doc['filename'] or '') or (doc['filepath'] or '')
    return os.path.splitext(name.split('?')[0])[1].lower()


def _applicant_documents(conn, applicant_id):
    # Fixed order so the position in this list can be used as the document's id
    return conn.execute(
        'SELECT * FROM applicant_documents WHERE applicant_id = ? AND replaced_at IS NULL '
        'ORDER BY document_type, filename, filepath', (applicant_id,)
    ).fetchall()


@app.route('/admin/job/<int:job_id>/applicant/<int:applicant_id>', methods=['GET', 'POST'])
def admin_applicant_detail(job_id, applicant_id):
    if 'admin' not in session:
        return redirect(url_for('admin_login'))

    conn = get_db()
    job = conn.execute('SELECT * FROM job_postings WHERE id = ?', (job_id,)).fetchone()
    applicant = conn.execute(
        'SELECT * FROM applicants WHERE id = ? AND job_id = ?', (applicant_id, job_id)
    ).fetchone()
    docs = _applicant_documents(conn, applicant_id) if applicant else []
    replaced_docs = []
    if applicant:
        for r in conn.execute('SELECT document_type, filename, replaced_at FROM applicant_documents '
                              'WHERE applicant_id = ? AND replaced_at IS NOT NULL ORDER BY replaced_at',
                              (applicant_id,)).fetchall():
            try:
                when = (datetime.fromisoformat(str(r['replaced_at'])) + timedelta(hours=8)).strftime('%b %d, %Y %I:%M %p')
            except ValueError:
                when = ''
            replaced_docs.append({'type': r['document_type'],
                                  'name': _display_filename(r['filename'], r['document_type']), 'when': when})

    related_apps = []
    if applicant:
        for m in PeopleIndex(_all_applications(conn)).related(dict(applicant)):
            o = m['other']
            related_apps.append({'id': o['id'], 'job_id': o['job_id'], 'name': o['full_name'],
                                 'control_number': o['control_number'], 'job_title': o['job_title'],
                                 'campus': o['job_campus'], 'applied_at': o['applied_at'], 'status': _status_label(o),
                                 'reasons': m['reasons'], 'strong': m['strong'], 'same_position': m['same_position']})

    # HR clicked "Ask AI for a note". The AI only advises; it never changes the group.
    ai_note = None
    ai_error = None
    if (request.method == 'POST' and job and applicant
            and applicant['screening_result'] == 'under_review'):
        try:
            documents_text = load_documents_text(conn, applicant_id)
            open_items = json.loads(applicant['screening_weaknesses'] or '[]')
            verified_items = json.loads(applicant['screening_strengths'] or '[]')
            ai_note = advise_applicant(documents_text, build_job_specs(job), open_items, verified_items)
            log_action('ai.advice', 'applicant', applicant_id, 'advisory note requested')
        except Exception as e:
            print(f"AI note failed for applicant {applicant_id}: {e}")
            ai_error = 'The AI helper could not be reached. Please try again in a moment.'
    conn.close()

    if not job or not applicant:
        return "Not found", 404

    hr_when = ''
    try:
        if _col(applicant, 'hr_decision_at'):
            hr_when = (datetime.fromisoformat(applicant['hr_decision_at']) + timedelta(hours=8)).strftime('%b %d, %Y %I:%M %p')
    except ValueError:
        hr_when = ''

    doc_list = [{
        'idx': i,
        'type': d['document_type'],
        'filename': d['filename'],
        'kind': _doc_kind(_doc_ext(d)),
    } for i, d in enumerate(docs)]

    wanted = request.args.get('doc', default=0, type=int)
    selected = doc_list[wanted] if 0 <= wanted < len(doc_list) else (doc_list[0] if doc_list else None)

    try:
        extra_answers = json.loads(_col(applicant, 'extra_answers') or '[]')
    except (ValueError, TypeError):
        extra_answers = []

    return render_template('admin/applicant_detail.html',
                           job=job, a=applicant, docs=doc_list, selected=selected,
                           ai_note=ai_note, ai_error=ai_error, hr_when=hr_when,
                           replaced_docs=replaced_docs, related_apps=related_apps,
                           extra_answers=extra_answers)


@app.route('/admin/job/<int:job_id>/applicant/<int:applicant_id>/document/<int:idx>')
def admin_view_document(job_id, applicant_id, idx):
    # Documents are fetched by the server and shown only to logged-in staff,
    # so applicants' files are never exposed through a public link.
    if 'admin' not in session:
        return redirect(url_for('admin_login'))

    conn = get_db()
    applicant = conn.execute(
        'SELECT * FROM applicants WHERE id = ? AND job_id = ?', (applicant_id, job_id)
    ).fetchone()
    docs = _applicant_documents(conn, applicant_id) if applicant else []
    conn.close()

    if not applicant or idx < 0 or idx >= len(docs):
        return "Not found", 404

    doc = docs[idx]
    host = urlparse(doc['filepath'] or '').hostname or ''
    if not host.endswith('cloudinary.com'):
        return "Document location not allowed", 400

    try:
        resp = requests.get(doc['filepath'], timeout=20)
    except requests.RequestException:
        return "The document could not be loaded. Please try again.", 502
    if resp.status_code != 200:
        return "The document could not be loaded.", 502

    ext = _doc_ext(doc)

    if ext == '.docx':
        # Browsers cannot display Word files, so convert to plain HTML on the server.
        # The page is sandboxed: no scripts, no outside requests.
        try:
            import mammoth
            body = mammoth.convert_to_html(BytesIO(resp.content)).value
        except Exception:
            return "This Word file could not be previewed. Please download it instead.", 502
        page = ('<!doctype html><meta charset="utf-8"><style>'
                'body{font-family:Arial,sans-serif;max-width:800px;margin:24px auto;padding:0 16px;'
                'line-height:1.5;color:#222}table{border-collapse:collapse}'
                'td,th{border:1px solid #ccc;padding:4px 8px}img{max-width:100%}</style>' + body)
        out = Response(page, mimetype='text/html')
        out.headers['Content-Security-Policy'] = ("sandbox; default-src 'none'; "
                                                  "img-src data:; style-src 'unsafe-inline'")
        out.headers['X-Content-Type-Options'] = 'nosniff'
        out.headers['X-Frame-Options'] = 'SAMEORIGIN'
        return out

    known = ext in DOC_MIMETYPES
    out = send_file(
        BytesIO(resp.content),
        mimetype=DOC_MIMETYPES.get(ext, 'application/octet-stream'),
        as_attachment=not known,          # unknown file types are downloaded, never rendered
        download_name=doc['filename'] or 'document',
    )
    out.headers['X-Content-Type-Options'] = 'nosniff'
    out.headers['X-Frame-Options'] = 'SAMEORIGIN'
    return out


def load_documents_text(conn, applicant_id):
    docs = conn.execute(
        'SELECT * FROM applicant_documents WHERE applicant_id = ? AND replaced_at IS NULL',
        (applicant_id,)
    ).fetchall()

    documents_text = {}
    for doc in docs:
        text = extract_text_from_pdf(doc['filepath']) or ''
        if doc['document_type'] in documents_text:
            documents_text[doc['document_type']] += "\n\n--- Additional document ---\n\n" + text
        else:
            documents_text[doc['document_type']] = text
    return documents_text


AI_TIME_BUDGET = 30    # seconds of fresh AI reading allowed in one request (the host has a time limit)
HR_LABELS = {'qualified': 'Qualified', 'unqualified': 'Disqualified'}


def _col(row, key):
    """A column that may not exist yet on an old row."""
    try:
        return row[key]
    except (KeyError, IndexError):
        return None


def documents_fingerprint(documents_text, terms):
    """Changes when a document or the posting's skill terms change, so the saved AI reading is reused
    only while it is still about the same documents."""
    blob = json.dumps({'docs': documents_text, 'terms': terms}, sort_keys=True)
    return hashlib.sha256(blob.encode('utf-8')).hexdigest()


def screen_one_applicant(conn, applicant, job_specs, allow_ai=True, force_reread=False):
    """
    1. The AI READS the documents once and the reading is saved (reused on every Re-screen
       while the documents are unchanged, so the result stays the same).
    2. Plain code CHECKS every claim in the reading against the real documents and applies the
       fixed checklist. Same documents + same saved reading = same result, every time.
    Returns how the AI was used: 'saved', 'fresh', 'failed' or 'skipped'.
    """
    documents_text = load_documents_text(conn, applicant['id'])

    # "Present" in a work history means the day the applicant applied.
    try:
        as_of = date.fromisoformat(str(applicant['applied_at'])[:10])
    except (ValueError, TypeError, KeyError):
        as_of = None

    terms = posting_terms(job_specs)
    fingerprint = documents_fingerprint(documents_text, terms)
    reading, how = None, 'skipped'

    saved = _col(applicant, 'ai_reading')
    if saved and _col(applicant, 'ai_reading_hash') == fingerprint and not force_reread:
        try:
            reading, how = json.loads(saved), 'saved'
        except ValueError:
            reading = None
    if reading is None and allow_ai and any((t or '').strip() for t in documents_text.values()):
        try:
            reading = read_documents(documents_text, terms)
            conn.execute('UPDATE applicants SET ai_reading = ?, ai_reading_hash = ? WHERE id = ?',
                         (json.dumps(reading), fingerprint, applicant['id']))
            how = 'fresh'
        except Exception as e:
            print(f"AI reading failed for applicant {applicant['id']}: {e}")
            reading, how = None, 'failed'

    outcome = decide(job_specs, documents_text, as_of, reading)

    weaknesses = list(outcome['weaknesses'])
    if how in ('failed', 'skipped') and any((t or '').strip() for t in documents_text.values()):
        weaknesses.append('The AI reading was not used for this result (the AI service was not available '
                          'or time ran out). Only the plain checklist was applied. Click Re-screen to add it.')

    final_result, reason = outcome['result'], outcome['reason']
    hr_decision = _col(applicant, 'hr_decision')
    if hr_decision in HR_LABELS:
        # HR already decided this one: Re-screen refreshes the checklist but never undoes HR's decision.
        final_result = hr_decision
        reason = (f"HR decision: {HR_LABELS[hr_decision]} by {_col(applicant, 'hr_decision_by') or 'HR'} - "
                  f"\"{_col(applicant, 'hr_decision_reason') or ''}\". "
                  f"The checklist result was {outcome['result'].replace('unqualified', 'disqualified')}: {outcome['reason']}")

    # Plain facts for HR's Excel sheet (they never change the group)
    try:
        profile = applicant_profile(documents_text, as_of, reading)
    except Exception as e:
        print(f"Profile facts failed for applicant {applicant['id']}: {e}")
        profile = {'education': None, 'experience_months': None}

    conn.execute('''
        UPDATE applicants SET
        screening_score = ?,
        screening_result = ?,
        screening_reason = ?,
        screening_missing = ?,
        screening_strengths = ?,
        screening_weaknesses = ?,
        system_result = ?,
        education_found = ?,
        experience_months = ?,
        is_screened = 1
        WHERE id = ?
    ''', (
        outcome['met_count'],
        final_result,               # qualified / unqualified (disqualified) / under_review
        reason,
        json.dumps(outcome['auto_fail_reasons']),
        json.dumps(outcome['strengths']),
        json.dumps(weaknesses),
        outcome['result'],          # what the checklist said, kept even after HR decides
        profile['education'],
        None if profile['experience_months'] is None else str(profile['experience_months']),
        applicant['id']
    ))
    conn.commit()
    return how


def screen_many(conn, applicants, job_specs):
    """Screen a list of applicants. Fresh AI readings stop once the time budget is used up;
    the rest get the plain checklist and can be re-screened later to add the AI reading."""
    started = time.time()
    failed = no_ai = 0
    for applicant in applicants:
        try:
            how = screen_one_applicant(conn, applicant, job_specs,
                                       allow_ai=(time.time() - started) < AI_TIME_BUDGET)
            if how in ('failed', 'skipped'):
                no_ai += 1
        except Exception as e:
            failed += 1
            print(f"Screening failed for applicant {applicant['id']}: {e}")
    return failed, no_ai


def screening_message(done_text, failed, no_ai):
    msg = done_text
    if failed:
        msg = f'Screening finished, but {failed} applicant(s) could not be screened. Please try them again.'
    if no_ai:
        msg += (f' {no_ai} applicant(s) got the plain checklist only, because the AI reading was not '
                f'available or time ran out. Click Re-screen on them to add it.')
    return msg


def build_job_specs(job):
    mandatory = json.loads(job['mandatory_requirements']) if job['mandatory_requirements'] else []
    return {
        'title': job['title'],
        'campus': job['campus'],
        'skills': job['skills'],
        'experience': job['experience'],
        'education': job['education'],
        'other_requirements': job['other_requirements'],
        'mandatory_requirements': mandatory,
        'required_documents': json.loads(job['required_documents']) if job['required_documents'] else []
    }

@app.route('/admin/job/<int:job_id>/screen')
def admin_screen(job_id):
    if 'admin' not in session:
        return redirect(url_for('admin_login'))

    conn = get_db()
    job = conn.execute('SELECT * FROM job_postings WHERE id = ?', (job_id,)).fetchone()
    applicants = conn.execute(
        'SELECT * FROM applicants WHERE job_id = ? AND is_screened = 0', (job_id,)
    ).fetchall()

    job_specs = build_job_specs(job)

    failed, no_ai = screen_many(conn, applicants, job_specs)

    conn.close()
    flash(screening_message('Screening complete!', failed, no_ai))
    return redirect(url_for('admin_applicants', job_id=job_id))

@app.route('/admin/job/<int:job_id>/rescreen-all')
def admin_rescreen_all(job_id):
    if 'admin' not in session:
        return redirect(url_for('admin_login'))

    conn = get_db()
    job = conn.execute('SELECT * FROM job_postings WHERE id = ?', (job_id,)).fetchone()
    applicants = conn.execute(
        'SELECT * FROM applicants WHERE job_id = ?', (job_id,)
    ).fetchall()

    job_specs = build_job_specs(job)

    failed, no_ai = screen_many(conn, applicants, job_specs)

    conn.close()
    flash(screening_message('All applicants re-screened!', failed, no_ai))
    return redirect(url_for('admin_applicants', job_id=job_id))

@app.route('/admin/job/<int:job_id>/applicant/<int:applicant_id>/rescreen')
def admin_rescreen_one(job_id, applicant_id):
    if 'admin' not in session:
        return redirect(url_for('admin_login'))

    conn = get_db()
    job = conn.execute('SELECT * FROM job_postings WHERE id = ?', (job_id,)).fetchone()
    applicant = conn.execute(
        'SELECT * FROM applicants WHERE id = ? AND job_id = ?', (applicant_id, job_id)
    ).fetchone()

    if not job or not applicant:
        conn.close()
        return "Not found", 404

    job_specs = build_job_specs(job)
    try:
        how = screen_one_applicant(conn, applicant, job_specs)
        flash({'saved': 'Re-screened using the saved AI reading. The AI was not asked again, so the result is the same.',
               'fresh': 'The AI read the documents, its claims were checked against them, and the applicant was screened.',
               }.get(how, 'Re-screened with the plain checklist only, because the AI reading was not available.'))
    except Exception as e:
        print(f"Screening failed for applicant {applicant_id}: {e}")
        flash('This applicant could not be screened. Please try again.')
    conn.close()

    return redirect(url_for('admin_applicants', job_id=job_id))

@app.route('/admin/job/<int:job_id>/applicant/<int:applicant_id>/reread')
def admin_reread_one(job_id, applicant_id):
    """Ask the AI to read this applicant's documents again (replaces the saved reading)."""
    if 'admin' not in session:
        return redirect(url_for('admin_login'))

    conn = get_db()
    job = conn.execute('SELECT * FROM job_postings WHERE id = ?', (job_id,)).fetchone()
    applicant = conn.execute(
        'SELECT * FROM applicants WHERE id = ? AND job_id = ?', (applicant_id, job_id)
    ).fetchone()
    if not job or not applicant:
        conn.close()
        return "Not found", 404

    try:
        how = screen_one_applicant(conn, applicant, build_job_specs(job), force_reread=True)
        flash('The AI read the documents again and the applicant was re-screened.' if how == 'fresh'
              else 'The AI could not read the documents right now, so only the plain checklist was applied.')
        log_action('ai.reread', 'applicant', applicant_id, f'how={how}')
    except Exception as e:
        print(f"Re-read failed for applicant {applicant_id}: {e}")
        flash('This applicant could not be re-read. Please try again.')
    conn.close()
    return redirect(url_for('admin_applicant_detail', job_id=job_id, applicant_id=applicant_id))


@app.route('/admin/job/<int:job_id>/applicant/<int:applicant_id>/decide', methods=['POST'])
def admin_hr_decide(job_id, applicant_id):
    """HR sets or changes an applicant's status to Qualified or Disqualified.
    Works for any screened applicant (Under review, Qualified or Disqualified). The checklist's own
    result is always kept in system_result, and every change is written to the audit log with the
    old and new status."""
    if 'admin' not in session:
        return redirect(url_for('admin_login'))

    decision = request.form.get('decision')
    reason = (request.form.get('reason') or '').strip()[:500]
    back = redirect(url_for('admin_applicant_detail', job_id=job_id, applicant_id=applicant_id))
    if decision not in HR_LABELS or len(reason) < 5:
        flash('Please choose Qualified or Disqualified and write a short reason (at least 5 characters).')
        return back

    conn = get_db()
    job = conn.execute('SELECT * FROM job_postings WHERE id = ?', (job_id,)).fetchone()
    applicant = conn.execute(
        'SELECT * FROM applicants WHERE id = ? AND job_id = ?', (applicant_id, job_id)
    ).fetchone()
    if not job or not applicant or not applicant['is_screened']:
        conn.close()
        flash('Screen this applicant first, then record the HR decision.')
        return back

    old_status = applicant['screening_result']
    if old_status == decision:
        conn.close()
        flash(f'This applicant is already {HR_LABELS[decision]}.')
        return back

    # The checklist result stays on record. If HR already decided before, system_result holds it.
    checklist_result = _col(applicant, 'system_result') if _col(applicant, 'hr_decision') else old_status
    checklist_result = checklist_result or old_status

    who = session.get('admin', 'HR')
    new_reason = (f'HR decision: {HR_LABELS[decision]} by {who} - "{reason}". '
                  f'The checklist result was {checklist_result.replace("unqualified", "disqualified").replace("_", " ")}: '
                  f'{applicant["screening_reason"]}')
    conn.execute('''
        UPDATE applicants SET
        hr_decision = ?, hr_decision_reason = ?, hr_decision_by = ?, hr_decision_at = ?,
        system_result = ?, screening_result = ?, screening_reason = ?
        WHERE id = ?
    ''', (decision, reason, who, datetime.utcnow().isoformat(timespec='seconds'),
          checklist_result, decision, new_reason, applicant_id))
    conn.commit()
    try:        # rebuild the reason text from the fresh checklist (reuses the saved AI reading, no AI call)
        fresh = conn.execute('SELECT * FROM applicants WHERE id = ?', (applicant_id,)).fetchone()
        screen_one_applicant(conn, fresh, build_job_specs(job), allow_ai=False)
    except Exception as e:
        print(f"Refreshing the checklist after the HR decision failed for applicant {applicant_id}: {e}")
    conn.close()
    log_action('hr.decision', 'applicant', applicant_id,
               f'old_status={old_status}; new_status={decision}; checklist_result={checklist_result}; '
               f'reason={reason[:200]}')
    flash(f'Status changed from {STATUS_LABELS.get(old_status, old_status)} to {HR_LABELS[decision]}.')
    return back


@app.route('/admin/job/<int:job_id>/applicant/<int:applicant_id>/undo-decision', methods=['POST'])
def admin_hr_undo(job_id, applicant_id):
    """HR takes back a decision. The applicant returns to the checklist's own result.
    No reason is asked; the audit log still records who undid what, and when."""
    if 'admin' not in session:
        return redirect(url_for('admin_login'))

    back = redirect(url_for('admin_applicant_detail', job_id=job_id, applicant_id=applicant_id))
    conn = get_db()
    job = conn.execute('SELECT * FROM job_postings WHERE id = ?', (job_id,)).fetchone()
    applicant = conn.execute(
        'SELECT * FROM applicants WHERE id = ? AND job_id = ?', (applicant_id, job_id)
    ).fetchone()
    if not job or not applicant or not _col(applicant, 'hr_decision'):
        conn.close()
        flash('This applicant has no HR decision to undo.')
        return back

    previous = applicant['hr_decision']
    conn.execute('''
        UPDATE applicants SET
        hr_decision = NULL, hr_decision_reason = NULL, hr_decision_by = NULL, hr_decision_at = NULL,
        screening_result = ?, screening_reason = ?
        WHERE id = ?
    ''', (_col(applicant, 'system_result') or 'under_review',
          'HR decision undone. Click Re-screen to refresh the checklist details.', applicant_id))
    conn.commit()
    try:        # refresh the checklist details straight away (reuses the saved AI reading)
        fresh = conn.execute('SELECT * FROM applicants WHERE id = ?', (applicant_id,)).fetchone()
        screen_one_applicant(conn, fresh, build_job_specs(job))
    except Exception as e:
        print(f"Re-screen after undo failed for applicant {applicant_id}: {e}")
    conn.close()
    log_action('hr.decision_undone', 'applicant', applicant_id, f'previous_decision={previous}')
    flash('HR decision undone. The applicant is back to the checklist result.')
    return back


@app.route('/admin/job/<int:job_id>/generate-ad')
def admin_generate_ad(job_id):
    if 'admin' not in session:
        return redirect(url_for('admin_login'))

    conn = get_db()
    job = conn.execute('SELECT * FROM job_postings WHERE id = ?', (job_id,)).fetchone()
    conn.close()

    if not job:
        return "Job not found", 404

    return render_template('admin/ad_result.html', job=dict(job))

@app.route('/admin/job/<int:job_id>/ad.png')
def admin_ad_image(job_id):
    # Rendered in memory on every request: Vercel's filesystem is read-only,
    # so nothing is saved to static/ads anymore.
    if 'admin' not in session:
        return redirect(url_for('admin_login'))

    conn = get_db()
    job = conn.execute('SELECT * FROM job_postings WHERE id = ?', (job_id,)).fetchone()
    conn.close()

    if not job:
        return "Job not found", 404

    job = dict(job)
    buf = generate_job_ad_image(job)
    safe = "".join(c if c.isalnum() else "_" for c in job['title'])
    return send_file(buf, mimetype='image/png',
                     as_attachment=request.args.get('download') == '1',
                     download_name=f"BulSU_Hiring_{safe}.png")

@app.route('/admin/job/<int:job_id>/download/documents/<int:applicant_id>')
def download_applicant_documents(job_id, applicant_id):
    if 'admin' not in session:
        return redirect(url_for('admin_login'))

    import zipfile
    from io import BytesIO

    conn = get_db()
    applicant = conn.execute('SELECT * FROM applicants WHERE id = ? AND job_id = ?',
                            (applicant_id, job_id)).fetchone()
    docs = conn.execute('SELECT * FROM applicant_documents WHERE applicant_id = ? AND replaced_at IS NULL',
                       (applicant_id,)).fetchall()
    conn.close()

    if not applicant or not docs:
        flash('No documents found for this applicant.')
        return redirect(url_for('admin_applicants', job_id=job_id))

    zip_buffer = BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
        for doc in docs:
            try:
                response = requests.get(doc['filepath'])
                if response.status_code == 200:
                    arcname = f"{applicant['full_name']}/{doc['filename']}"
                    zip_file.writestr(arcname, response.content)
            except Exception:
                pass

    zip_buffer.seek(0)
    filename = f"{applicant['full_name'].replace(' ', '_')}_documents.zip"
    return send_file(zip_buffer, mimetype='application/zip', as_attachment=True,
                    download_name=filename)

@app.route('/admin/job/<int:job_id>/download/all-documents')
def download_all_documents(job_id):
    if 'admin' not in session:
        return redirect(url_for('admin_login'))

    import zipfile
    from io import BytesIO

    conn = get_db()
    job = conn.execute('SELECT * FROM job_postings WHERE id = ?', (job_id,)).fetchone()
    applicants = conn.execute('SELECT * FROM applicants WHERE job_id = ?', (job_id,)).fetchall()
    conn.close()

    if not applicants:
        flash('No applicants found.')
        return redirect(url_for('admin_applicants', job_id=job_id))

    zip_buffer = BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
        for applicant in applicants:
            conn = get_db()
            docs = conn.execute('SELECT * FROM applicant_documents WHERE applicant_id = ? AND replaced_at IS NULL',
                               (applicant['id'],)).fetchall()
            conn.close()

            for doc in docs:
                try:
                    response = requests.get(doc['filepath'])
                    if response.status_code == 200:
                        arcname = f"{applicant['full_name']}/{doc['document_type']}/{doc['filename']}"
                        zip_file.writestr(arcname, response.content)
                except Exception:
                    pass

    zip_buffer.seek(0)
    filename = f"{job['title'].replace(' ', '_')}_all_documents.zip"
    return send_file(zip_buffer, mimetype='application/zip', as_attachment=True,
                    download_name=filename)

@app.route('/admin/job/<int:job_id>/download/qualified')
def download_qualified(job_id):
    if 'admin' not in session:
        return redirect(url_for('admin_login'))

    conn = get_db()
    job = conn.execute('SELECT * FROM job_postings WHERE id = ?', (job_id,)).fetchone()
    applicants = conn.execute(
        "SELECT * FROM applicants WHERE job_id = ? AND screening_result = 'qualified'",
        (job_id,)
    ).fetchall()
    conn.close()

    applicants = [dict(a) for a in applicants]
    buffer = export_qualified(applicants, dict(job) if job else None)

    if not buffer:
        flash('No qualified applicants to download.')
        return redirect(url_for('admin_applicants', job_id=job_id))

    return send_file(buffer, as_attachment=True, download_name="qualified_applicants.xlsx",
                      mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

@app.route('/admin/job/<int:job_id>/download/all')
def download_all(job_id):
    if 'admin' not in session:
        return redirect(url_for('admin_login'))

    conn = get_db()
    job = conn.execute('SELECT * FROM job_postings WHERE id = ?', (job_id,)).fetchone()
    applicants = conn.execute(
        'SELECT * FROM applicants WHERE job_id = ? AND is_screened = 1',
        (job_id,)
    ).fetchall()
    conn.close()

    applicants = [dict(a) for a in applicants]
    buffer = export_all(applicants, dict(job) if job else None)

    if not buffer:
        flash('No screened applicants to download.')
        return redirect(url_for('admin_applicants', job_id=job_id))

    return send_file(buffer, as_attachment=True, download_name="all_applicants.xlsx",
                      mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

def _file_slug(text):
    return re.sub(r'[^A-Za-z0-9]+', '_', str(text or 'positions')).strip('_')[:40] or 'positions'


@app.route('/admin/job/<int:job_id>/download/applicants')
def download_applicant_list(job_id):
    """Excel list of ALL applicants for one position, including those not screened yet."""
    if 'admin' not in session:
        return redirect(url_for('admin_login'))

    conn = get_db()
    job = conn.execute('SELECT * FROM job_postings WHERE id = ?', (job_id,)).fetchone()
    if not job:
        conn.close()
        return "Job not found", 404
    applicants = conn.execute(
        'SELECT * FROM applicants WHERE job_id = ? ORDER BY applied_at, id', (job_id,)
    ).fetchall()
    conn.close()

    buffer = export_applicant_list([dict(a) for a in applicants], dict(job))
    if not buffer:
        flash('There are no applicants for this position yet.')
        return redirect(url_for('admin_applicants', job_id=job_id))

    log_action('export.applicants', 'job', job_id, f'list of all applicants ({len(applicants)} row(s))')
    return send_file(buffer, as_attachment=True,
                     download_name=f"applicants_{_file_slug(job['title'])}.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.route('/admin/download/applicants-by-position')
def download_applicants_by_position():
    """One Excel workbook: a sheet for each open position, listing all of its applicants."""
    if 'admin' not in session:
        return redirect(url_for('admin_login'))

    conn = get_db()
    jobs = conn.execute(
        'SELECT * FROM job_postings WHERE is_active = 1 AND archived_at IS NULL ORDER BY campus, title'
    ).fetchall()
    groups, total = [], 0
    for job in jobs:
        apps = conn.execute(
            'SELECT * FROM applicants WHERE job_id = ? ORDER BY applied_at, id', (job['id'],)
        ).fetchall()
        total += len(apps)
        groups.append((dict(job), [dict(a) for a in apps]))
    conn.close()

    buffer = export_applicants_by_position(groups)
    if not buffer:
        flash('There are no applicants for the open positions yet.')
        return redirect(url_for('admin_home'))

    log_action('export.applicants', 'job', 'all-open', f'{len(groups)} open position(s), {total} applicant row(s)')
    return send_file(buffer, as_attachment=True, download_name="applicants_all_open_positions.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)