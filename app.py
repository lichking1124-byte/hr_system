from flask import Flask, render_template, request, send_file, redirect, url_for, session, flash, Response
import os
import json
import requests
from io import BytesIO
from urllib.parse import urlparse
from datetime import datetime, timedelta
from database import get_db, init_db
from resume_parser import extract_text_from_pdf
from ai_screener import screen_applicant
from screening_rules import decide
from exporter import export_qualified, export_all
from ad_generator import generate_job_ad_image
from cloudinary_uploader import upload_file
from config import SECRET_KEY, CAMPUSES
from auth import init_auth, log_action, is_locked_out, utcnow, LOCKOUT_MINUTES
from accounts import accounts
from passwords import verify_password
from doc_presets import PRESETS, DOC_HINTS, VALID_TYPES

app = Flask(__name__)
app.secret_key = SECRET_KEY

try:
    os.makedirs("static/uploads", exist_ok=True)
    os.makedirs("static/ads", exist_ok=True)
except OSError:
    pass

init_db()


def ensure_position_type_column():
    """Adds job_postings.position_type (academic / non_academic) if it is missing."""
    conn = get_db()
    try:
        conn.execute("ALTER TABLE job_postings ADD COLUMN IF NOT EXISTS position_type TEXT")
        conn.commit()
    except Exception as e:
        print(f"position_type column check failed: {e}")
    finally:
        conn.close()


ensure_position_type_column()


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
    return render_template('applicant/job_detail.html', job=job, required_docs=required_docs)

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

    if request.method == 'POST':
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

        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO applicants (job_id, full_name, email, phone)
            VALUES (?, ?, ?, ?)
        ''', (job_id, full_name, email, phone))
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
                           hints=DOC_HINTS)

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
                }

    return render_template('applicant/status.html', result=result, searched=searched)

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
             deadline, position_type)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            clean_position_type(request.form.get('position_type'))
        ))
        conn.commit()
        conn.close()
        flash('Job posting created successfully!')
        return redirect(url_for('admin_home'))

    return render_template('admin/job_form.html', job=None, campuses=CAMPUSES, mandatory=[],
                           presets=PRESETS)

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
            deadline=?, position_type=?
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
            job_id
        ))
        conn.commit()
        conn.close()
        flash('Job posting updated successfully!')
        return redirect(url_for('admin_home'))

    required_docs = json.loads(job['required_documents']) if job['required_documents'] else []
    mandatory = json.loads(job['mandatory_requirements']) if job['mandatory_requirements'] else []
    return render_template('admin/job_form.html', job=job, required_docs=required_docs,
                           mandatory=mandatory, campuses=CAMPUSES, presets=PRESETS)

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
    conn.close()

    return render_template('admin/applicants.html', job=job, applicants=applicants)

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
        'SELECT * FROM applicant_documents WHERE applicant_id = ? '
        'ORDER BY document_type, filename, filepath', (applicant_id,)
    ).fetchall()


@app.route('/admin/job/<int:job_id>/applicant/<int:applicant_id>')
def admin_applicant_detail(job_id, applicant_id):
    if 'admin' not in session:
        return redirect(url_for('admin_login'))

    conn = get_db()
    job = conn.execute('SELECT * FROM job_postings WHERE id = ?', (job_id,)).fetchone()
    applicant = conn.execute(
        'SELECT * FROM applicants WHERE id = ? AND job_id = ?', (applicant_id, job_id)
    ).fetchone()
    docs = _applicant_documents(conn, applicant_id) if applicant else []
    conn.close()

    if not job or not applicant:
        return "Not found", 404

    doc_list = [{
        'idx': i,
        'type': d['document_type'],
        'filename': d['filename'],
        'kind': _doc_kind(_doc_ext(d)),
    } for i, d in enumerate(docs)]

    wanted = request.args.get('doc', default=0, type=int)
    selected = doc_list[wanted] if 0 <= wanted < len(doc_list) else (doc_list[0] if doc_list else None)

    return render_template('admin/applicant_detail.html',
                           job=job, a=applicant, docs=doc_list, selected=selected)


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
        log_action('document.view', 'applicant', applicant_id, f"{doc['document_type']}")
        return out

    known = ext in DOC_MIMETYPES
    log_action('document.view', 'applicant', applicant_id, f"{doc['document_type']}")
    out = send_file(
        BytesIO(resp.content),
        mimetype=DOC_MIMETYPES.get(ext, 'application/octet-stream'),
        as_attachment=not known,          # unknown file types are downloaded, never rendered
        download_name=doc['filename'] or 'document',
    )
    out.headers['X-Content-Type-Options'] = 'nosniff'
    out.headers['X-Frame-Options'] = 'SAMEORIGIN'
    return out


def screen_one_applicant(conn, applicant, job_specs):
    docs = conn.execute(
        'SELECT * FROM applicant_documents WHERE applicant_id = ?',
        (applicant['id'],)
    ).fetchall()

    documents_text = {}
    for doc in docs:
        text = extract_text_from_pdf(doc['filepath']) or ''
        if doc['document_type'] in documents_text:
            documents_text[doc['document_type']] += "\n\n--- Additional document ---\n\n" + text
        else:
            documents_text[doc['document_type']] = text

    # The AI only reads the documents and quotes evidence.
    # Fixed rules (screening_rules.decide) make the actual decision.
    extraction = screen_applicant(documents_text, job_specs)
    outcome = decide(job_specs, extraction, documents_text)

    conn.execute('''
        UPDATE applicants SET
        screening_score = ?,
        screening_result = ?,
        screening_reason = ?,
        screening_missing = ?,
        screening_strengths = ?,
        screening_weaknesses = ?,
        is_screened = 1
        WHERE id = ?
    ''', (
        outcome['met_count'],
        'qualified' if outcome['qualified'] else 'unqualified',
        outcome['reason'],
        json.dumps(outcome['auto_fail_reasons']),
        json.dumps(outcome['strengths']),
        json.dumps(outcome['weaknesses']),
        applicant['id']
    ))
    conn.commit()

def build_job_specs(job):
    mandatory = json.loads(job['mandatory_requirements']) if job['mandatory_requirements'] else []
    return {
        'title': job['title'],
        'campus': job['campus'],
        'skills': job['skills'],
        'experience': job['experience'],
        'education': job['education'],
        'other_requirements': job['other_requirements'],
        'mandatory_requirements': mandatory
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

    failed = 0
    for applicant in applicants:
        try:
            screen_one_applicant(conn, applicant, job_specs)
        except Exception as e:
            failed += 1
            print(f"Screening failed for applicant {applicant['id']}: {e}")

    conn.close()
    flash('Screening complete!' if not failed else
          f'Screening finished, but {failed} applicant(s) could not be screened. Please try them again.')
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

    failed = 0
    for applicant in applicants:
        try:
            screen_one_applicant(conn, applicant, job_specs)
        except Exception as e:
            failed += 1
            print(f"Screening failed for applicant {applicant['id']}: {e}")

    conn.close()
    flash('All applicants re-screened!' if not failed else
          f'Re-screen finished, but {failed} applicant(s) could not be screened. Please try them again.')
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
        screen_one_applicant(conn, applicant, job_specs)
        flash('Applicant re-screened!')
    except Exception as e:
        print(f"Screening failed for applicant {applicant_id}: {e}")
        flash('This applicant could not be screened. Please try again.')
    conn.close()

    return redirect(url_for('admin_applicants', job_id=job_id))

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
    docs = conn.execute('SELECT * FROM applicant_documents WHERE applicant_id = ?',
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
            docs = conn.execute('SELECT * FROM applicant_documents WHERE applicant_id = ?',
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
    applicants = conn.execute(
        "SELECT * FROM applicants WHERE job_id = ? AND screening_result = 'qualified'",
        (job_id,)
    ).fetchall()
    conn.close()

    applicants = [dict(a) for a in applicants]
    buffer = export_qualified(applicants)

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
    applicants = conn.execute(
        'SELECT * FROM applicants WHERE job_id = ? AND is_screened = 1',
        (job_id,)
    ).fetchall()
    conn.close()

    applicants = [dict(a) for a in applicants]
    buffer = export_all(applicants)

    if not buffer:
        flash('No screened applicants to download.')
        return redirect(url_for('admin_applicants', job_id=job_id))

    return send_file(buffer, as_attachment=True, download_name="all_applicants.xlsx",
                      mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)