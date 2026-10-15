import pandas as pd
from io import BytesIO
from datetime import timedelta

PH_OFFSET = timedelta(hours=8)   # database stores UTC


def _fmt_timestamp(value):
    """applied_at comes back as a datetime from Postgres, but guard against
    None or a plain string just in case."""
    if not value:
        return ''
    if hasattr(value, 'strftime'):
        return (value + PH_OFFSET).strftime('%b %d, %Y %I:%M %p')
    return str(value)


def export_qualified(applicants):
    if not applicants:
        return None

    data = []
    for a in applicants:
        data.append({
            'Control No.': a.get('control_number') or '',
            'Full Name': a['full_name'],
            'Email': a['email'],
            'Phone': a['phone'],
            'Score': a['screening_score'],
            'Reason': a['screening_reason'],
            'Verified Documents': a['screening_result'],
            'Applied At': _fmt_timestamp(a.get('applied_at'))
        })

    df = pd.DataFrame(data)
    buffer = BytesIO()
    df.to_excel(buffer, index=False, engine='openpyxl')
    buffer.seek(0)
    return buffer

def export_all(applicants):
    if not applicants:
        return None

    data = []
    for a in applicants:
        data.append({
            'Control No.': a.get('control_number') or '',
            'Full Name': a['full_name'],
            'Email': a['email'],
            'Phone': a['phone'],
            'Score': a['screening_score'],
            'Status': 'Qualified' if a['screening_result'] == 'qualified' else 'Unqualified',
            'Reason': a['screening_reason'],
            'Applied At': _fmt_timestamp(a.get('applied_at'))
        })

    df = pd.DataFrame(data)
    buffer = BytesIO()
    df.to_excel(buffer, index=False, engine='openpyxl')
    buffer.seek(0)
    return buffer