import json
import re
from io import BytesIO
from datetime import timedelta

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

STATUS_LABELS = {'qualified': 'Qualified', 'unqualified': 'Disqualified', 'under_review': 'Under review'}
HR_LABELS = {'qualified': 'Qualified', 'unqualified': 'Disqualified'}
PH_OFFSET = timedelta(hours=8)   # database stores UTC

HEADER_FILL = PatternFill('solid', start_color='7B1E1E')
HEADER_FONT = Font(bold=True, color='FFFFFF', name='Calibri', size=11)
BODY_FONT = Font(name='Calibri', size=10)
THIN = Side(style='thin', color='D9D9D9')
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
STATUS_FILLS = {
    'Not screened yet': PatternFill('solid', start_color='E7E6E6'),
    'Qualified': PatternFill('solid', start_color='E2F0D9'),
    'Disqualified': PatternFill('solid', start_color='F8D7DA'),
    'Under review': PatternFill('solid', start_color='FFF2CC'),
}


def _fmt_timestamp(value):
    """applied_at comes back as a datetime from Postgres, but guard against
    None or a plain string just in case."""
    if not value:
        return ''
    if hasattr(value, 'strftime'):
        return (value + PH_OFFSET).strftime('%b %d, %Y %I:%M %p')
    return str(value)


def _lines(value):
    """screening_strengths / screening_weaknesses are stored as JSON lists.
    One item per line inside the cell, so nothing runs together."""
    if not value:
        return ''
    try:
        items = json.loads(value) if isinstance(value, str) else value
        return '\n'.join('• ' + str(i) for i in items)
    except (ValueError, TypeError):
        return str(value)


def _experience(value):
    """experience_months is saved when the applicant is screened (total dated work experience)."""
    if value in (None, ''):
        return 'Not available (re-screen)'
    try:
        months = float(value)
    except (ValueError, TypeError):
        return str(value)
    if months < 0.5:
        return 'None found'
    total = int(months + 0.5)
    years, rest = divmod(total, 12)
    parts = []
    if years:
        parts.append(f"{years} yr{'s' if years != 1 else ''}")
    if rest:
        parts.append(f"{rest} mo{'s' if rest != 1 else ''}")
    return ' '.join(parts)


def _education(value):
    if value is None:
        return 'Not available (re-screen)'
    return value or 'Not found in the diploma / transcript'


def _extra(value):
    """HR's extra questions and the applicant's answers, one per line."""
    if not value:
        return ''
    try:
        items = json.loads(value) if isinstance(value, str) else value
        return '\n'.join(f"{i.get('label', '')}: {i.get('answer', '') or '-'}" for i in items)
    except (ValueError, TypeError, AttributeError):
        return ''


def _screened(a):
    """Rows from the exports of screened applicants carry no flag; the full list does."""
    return bool(a.get('is_screened', 1))


def _status(a):
    if not _screened(a):
        return 'Not screened yet'
    return STATUS_LABELS.get(a.get('screening_result'), 'Under review')


def _checklist_result(a):
    """What the fixed checklist said, even if HR has since decided differently."""
    if not _screened(a):
        return ''
    return STATUS_LABELS.get(a.get('system_result') or a.get('screening_result'), 'Under review')


# (header, column width, value from one applicant row)
def _columns(job):
    title = (job or {}).get('title') or ''
    campus = (job or {}).get('campus') or ''
    return [
        ('Control No.', 18, lambda a: a.get('control_number') or ''),
        ('Full Name', 26, lambda a: a.get('full_name') or ''),
        ('Email', 30, lambda a: a.get('email') or ''),
        ('Phone', 16, lambda a: a.get('phone') or ''),
        ('Additional Information', 36, lambda a: _extra(a.get('extra_answers'))),
        ('Position', 28, lambda a: title),
        ('Campus', 18, lambda a: campus),
        ('Course / Degree (from diploma)', 36,
         lambda a: _education(a.get('education_found')) if _screened(a) else ''),
        ('Work Experience (dated)', 20,
         lambda a: _experience(a.get('experience_months')) if _screened(a) else ''),
        ('Status', 14, _status),
        ('Checklist Result', 16, _checklist_result),
        ('HR Decision', 14, lambda a: HR_LABELS.get(a.get('hr_decision'), '') if _screened(a) else ''),
        ('HR Decided By', 18, lambda a: a.get('hr_decision_by') or ''),
        ('HR Reason', 40, lambda a: a.get('hr_decision_reason') or ''),
        ('Applied At', 21, lambda a: _fmt_timestamp(a.get('applied_at'))),
        ('Reason', 60, lambda a: (a.get('screening_reason') or '') if _screened(a) else 'Not screened yet.'),
        ('Verified', 60, lambda a: _lines(a.get('screening_strengths')) if _screened(a) else ''),
        ('Not Verified / For Review', 60, lambda a: _lines(a.get('screening_weaknesses')) if _screened(a) else ''),
    ]


def _fill_sheet(ws, applicants, job):
    columns = _columns(job)
    for col, (header, width, _) in enumerate(columns, start=1):
        cell = ws.cell(row=1, column=col, value=header)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.border = BORDER
        cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
        ws.column_dimensions[get_column_letter(col)].width = width
    ws.row_dimensions[1].height = 32

    status_col = [h for h, _, _ in columns].index('Status') + 1
    for row, a in enumerate(applicants, start=2):
        for col, (_, _, getter) in enumerate(columns, start=1):
            cell = ws.cell(row=row, column=col, value=getter(a))
            cell.font = BODY_FONT
            cell.border = BORDER
            cell.alignment = Alignment(vertical='top', wrap_text=True)
        status_cell = ws.cell(row=row, column=status_col)
        fill = STATUS_FILLS.get(status_cell.value)
        if fill:
            status_cell.fill = fill
        status_cell.alignment = Alignment(horizontal='center', vertical='top', wrap_text=True)

    ws.freeze_panes = 'C2'          # header row and the first two columns stay visible
    ws.auto_filter.ref = ws.dimensions
    ws.sheet_view.zoomScale = 100
    # printing: landscape, all columns on the page width
    ws.page_setup.orientation = 'landscape'
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True


def _build(applicants, job, sheet_title):
    wb = Workbook()
    ws = wb.active
    ws.title = sheet_title
    _fill_sheet(ws, applicants, job)
    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer


def _sheet_name(text, used):
    """Excel sheet names: at most 31 characters, none of  [ ] : * ? / \\, and unique in the workbook."""
    base = re.sub(r'[\[\]:*?/\\]', ' ', str(text or 'Position'))
    base = re.sub(r'\s+', ' ', base).strip()[:31] or 'Position'
    name, n = base, 2
    while name.lower() in used:
        suffix = f' ({n})'
        name = base[:31 - len(suffix)] + suffix
        n += 1
    used.add(name.lower())
    return name


def export_qualified(applicants, job=None):
    if not applicants:
        return None
    return _build(applicants, job, 'Qualified applicants')


def export_all(applicants, job=None):
    if not applicants:
        return None
    return _build(applicants, job, 'All applicants')


def export_applicant_list(applicants, job=None):
    """Every applicant of one position, screened or not."""
    if not applicants:
        return None
    return _build(applicants, job, 'All applicants')


def export_applicants_by_position(groups):
    """One workbook, one sheet per open position. groups = [(job, [applicants]), ...]"""
    if not groups or not any(apps for _, apps in groups):
        return None
    wb = Workbook()
    used = set()
    first = True
    for job, apps in groups:
        ws = wb.active if first else wb.create_sheet()
        first = False
        title = f"{job.get('title') or 'Position'}"
        if job.get('campus'):
            title = f"{title} - {job['campus']}"
        ws.title = _sheet_name(title, used)
        _fill_sheet(ws, apps, job)
    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer
