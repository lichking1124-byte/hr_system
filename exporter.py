import pandas as pd
from io import BytesIO

def export_qualified(applicants):
    if not applicants:
        return None

    data = []
    for a in applicants:
        data.append({
            'Full Name': a['full_name'],
            'Email': a['email'],
            'Phone': a['phone'],
            'Score': a['screening_score'],
            'Reason': a['screening_reason'],
            'Verified Documents': a['screening_result']
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
            'Full Name': a['full_name'],
            'Email': a['email'],
            'Phone': a['phone'],
            'Score': a['screening_score'],
            'Status': 'Qualified' if a['screening_result'] == 'qualified' else 'Unqualified',
            'Reason': a['screening_reason']
        })

    df = pd.DataFrame(data)
    buffer = BytesIO()
    df.to_excel(buffer,