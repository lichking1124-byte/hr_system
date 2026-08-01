from flask import Flask, render_template, request, send_file, redirect, url_for, session, flash
import os
import json
from datetime import datetime, timedelta
from database import get_db, init_db
from resume_parser import extract_text_from_pdf
from ai_screener import screen_applicant
from exporter import export_qualified, export_all
from ad_generator import generate_job_ad
from cloudinary_uploader import upload_file
from config import SECRET_KEY, CAMPUSES

app = Flask(__name__)
app.secret_key = SECRET_KEY

try:
    os.makedirs("static/uploads", exist_ok=True)
    os.makedirs("static/ads", exist_ok=True)
except OSError:
    pass

init_db()

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

        for doc_type in required_docs:
            field_name = doc_type.replace(' ', '_').lower()
            if field_name in request.files:
                file = request.files[field_name]
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

        return render_template('applicant/success.html', name=full_name, job=job)

    return render_template('applicant/apply.html', job=job, required_docs=required_docs)

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
        username = request.form['username']
        password = request.form['password']

        conn = get_db()
        user = conn.execute(
            'SELECT * FROM admin_users WHERE username = ? AND password = ?',
            (username, password)
        ).fetchone()
        conn.close()

        if user:
            session['admin'] = username
            return redirect(url_for('admin_home'))
        else:
            flash('Invalid username or password')

    return render_template('admin/login.html')

@app.route('/admin/logout')
def admin_logout():
    session.pop('admin', None)
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
             education, other_requirements, required_documents, mandatory_requirements)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            request.form['campus'],
            request.form['title'],
            request.form['description'],
            request.form['skills'],
            request.form['experience'],
            request.form['education'],
            request.form['other_requirements'],
            json.dumps(required_docs),
            json.dumps(mandatory)
        ))
        conn.commit()
        conn.close()
        flash('Job posting created successfully!')
        return redirect(url_for('admin_home'))

    return render_template('admin/job_form.html', job=None, campuses=CAMPUSES, mandatory=[])

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
            required_documents=?, mandatory_requirements=?, is_active=?
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
            job_id
        ))
        conn.commit()
        conn.close()
        flash('Job posting updated successfully!')
        return redirect(url_for('admin_home'))

    required_docs = json.loads(job['required_documents']) if job['required_documents'] else []
    mandatory = json.loads(job['mandatory_requirements']) if job['mandatory_requirements'] else []
    return render_template('admin/job_form.html', job=job, required_docs=required_docs,
                           mandatory=mandatory, campuses=CAMPUSES)

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

@app.route('/admin/job/<int:job_id>/screen')
def admin_screen(job_id):
    if 'admin' not in session:
        return redirect(url_for('admin_login'))

    conn = get_db()
    job = conn.execute('SELECT * FROM job_postings WHERE id = ?', (job_id,)).fetchone()
    applicants = conn.execute(
        'SELECT * FROM applicants WHERE job_id = ? AND is_screened = 0', (job_id,)
    ).fetchall()

    mandatory = json.loads(job['mandatory_requirements']) if job['mandatory_requirements'] else []

    job_specs = {
        'title': job['title'],
        'campus': job['campus'],
        'skills': job['skills'],
        'experience': job['experience'],
        'education': job['education'],
        'other_requirements': job['other_requirements'],
        'mandatory_requirements': mandatory
    }

    for applicant in applicants:
        docs = conn.execute(
            'SELECT * FROM applicant_documents WHERE applicant_id = ?',
            (applicant['id'],)
        ).fetchall()

        documents_text = {}
        for doc in docs:
            text = extract_text_from_pdf(doc['filepath'])
            documents_text[doc['document_type']] = text

        result = screen_applicant(documents_text, job_specs)

        auto_failed = result.get('auto_failed', False)
        missing = result.get('missing', [])
        unverified = result.get('unverified_claims', [])
        combined = missing + [f"Unverified: {u}" for u in unverified]
        strengths = result.get('strengths', [])
        weaknesses = result.get('weaknesses', [])

        final_qualified = result['qualified'] and not auto_failed

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
            result['score'],
            'qualified' if final_qualified else 'unqualified',
            result['reason'],
            json.dumps(combined),
            json.dumps(strengths),
            json.dumps(weaknesses),
            applicant['id']
        ))
        conn.commit()

    conn.close()
    flash('Screening complete!')
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

    job = dict(job)
    filepath, filename = generate_job_ad(job)

    return render_template('admin/ad_result.html',
                           image=filename,
                           job=job)

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
            if os.path.exists(doc['filepath']):
                arcname = f"{applicant['full_name']}/{doc['filename']}"
                zip_file.write(doc['filepath'], arcname=arcname)

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
                if os.path.exists(doc['filepath']):
                    arcname = f"{applicant['full_name']}/{doc['document_type']}/{doc['filename']}"
                    zip_file.write(doc['filepath'], arcname=arcname)

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
        'SELECT * FROM applicants WHERE job_id = ? AND screening_result = "qualified"',
        (job_id,)
    ).fetchall()
    conn.close()

    applicants = [dict(a) for a in applicants]
    filename = export_qualified(applicants)

    if not filename:
        flash('No qualified applicants to download.')
        return redirect(url_for('admin_applicants', job_id=job_id))

    return send_file(filename, as_attachment=True)

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
    filename = export_all(applicants)

    if not filename:
        flash('No screened applicants to download.')
        return redirect(url_for('admin_applicants', job_id=job_id))

    return send_file(filename, as_attachment=True)

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)