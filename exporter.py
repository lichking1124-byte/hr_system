import pandas as pd

def export_qualified(applicants, filename="qualified_applicants.xlsx"):
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
    df.to_excel(filename, index=False, engine='openpyxl')
    return filename

def export_all(applicants, filename="all_applicants.xlsx"):
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
    df.to_excel(filename, index=False, engine='openpyxl')
    return filename