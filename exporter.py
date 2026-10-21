import json
import pandas as pd
from io import BytesIO
from datetime import timedelta

STATUS_LABELS = {'qualified': 'Qualified', 'unqualified': 'Disqualified', 'under_review': 'Under review'}
PH_OFFSET = timedelta(hours=8)   # database stores UTC


def _fmt_timestamp(value):
    """applied_at comes back as a datetime from Postgres, but guard against
    None or a plain string just in case."""
    if not value:
        return ''
    if hasattr(value, 'strftime'):
        return (value + PH_OFFSET).strftime('%b %d, %Y %I:%M %p')
    return str(value)


def _join_json(value):
    """screening_strengths / screening_weaknesses are stored as JSON lists."""
    if not value:
        return ''
    try:
        items = json.loads(value) if isinstance(value, str) else value
        return ' | '.join(str(i) for i in items)
    except (ValueError, TypeError):
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
            'Reason': a['screening_reason'],
            'Verified': _join_json(a.get('screening_strengths')),
            'Not Verified / For Review': _join_json(a.get('screening_weaknesses')),
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
            'Status': STATUS_LABELS.get(a['screening_result'], 'Under review'),
            'Reason': a['screening_reason'],
            'Verified': _join_json(a.get('screening_strengths')),
            'Not Verified / For Review': _join_json(a.get('screening_weaknesses')),
            'Applied At': _fmt_timestamp(a.get('applied_at'))
        })

    df = pd.DataFrame(data)
    buffer = BytesIO()
    df.to_excel(buffer, index=False, engine='openpyxl')
    buffer.seek(0)
    return buffer