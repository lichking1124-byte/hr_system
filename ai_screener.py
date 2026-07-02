from groq import Groq
import json
from config import GROQ_API_KEY

client = Groq(api_key=GROQ_API_KEY)

def screen_applicant(documents_text, job_specs):
    docs_summary = ""
    for doc_type, text in documents_text.items():
        docs_summary += f"\n--- {doc_type} ---\n{text}\n"

    mandatory = job_specs.get('mandatory_requirements', [])

    mandatory_rules = ""
    if 'education' in mandatory:
        mandatory_rules += f"""
- MANDATORY EDUCATION: The job requires "{job_specs['education']}".
  The applicant MUST submit a Diploma or Transcript of Records that CONFIRMS this degree.
  If no diploma/TOR is submitted OR if it does not match, set "auto_fail" to true and
  add "Auto-failed: Education requirement not verified — {job_specs['education']} required" to missing list.
"""
    if 'skills' in mandatory:
        mandatory_rules += f"""
- MANDATORY SKILLS: The job requires "{job_specs['skills']}".
  The applicant MUST provide certificates or documents proving these skills.
  If not verified, set "auto_fail" to true and add it to missing list.
"""
    if 'experience' in mandatory:
        mandatory_rules += f"""
- MANDATORY EXPERIENCE: The job requires "{job_specs['experience']}".
  The applicant MUST verify this through employment certificates or official documents.
  OJT/work immersion still counts for fresh graduates.
  If not verified, set "auto_fail" to true and add it to missing list.
"""
    if 'other' in mandatory:
        mandatory_rules += f"""
- MANDATORY OTHER REQUIREMENTS: The job requires "{job_specs['other_requirements']}".
  This includes licenses like PRC, eligibility requirements, etc.
  If not verified through submitted documents, set "auto_fail" to true and add it to missing list.
"""

    prompt = f"""
You are a strict but fair HR screening assistant for Bulacan State University (BulSU).
Your job is to evaluate applicants and highlight both their strengths and weaknesses.

JOB REQUIREMENTS:
- Position: {job_specs['title']}
- Campus: {job_specs['campus']}
- Required Skills: {job_specs['skills']}
- Minimum Experience: {job_specs['experience']}
- Education Required: {job_specs['education']}
- Other Requirements: {job_specs.get('other_requirements', 'None')}

SUBMITTED DOCUMENTS:
{docs_summary}

MANDATORY REQUIREMENTS (auto-fail if not verified):
{mandatory_rules if mandatory_rules else "No mandatory requirements set for this position."}

EVALUATION RULES:
1. BASIC SKILLS (communication, Microsoft Office, filing, customer service, teamwork):
   - Do NOT require proof unless marked mandatory
   - Accept at face value from resume

2. EDUCATION:
   - If marked mandatory, applicant MUST have verified diploma/TOR
   - If not mandatory, flag as unverified but don't fail them

3. SPECIALIZED TRAINING & CERTIFICATIONS:
   - PRC License, board exam, NC certificates — require proof if mandatory
   - If not mandatory, note as unverified

4. WORK EXPERIENCE:
   - Fresh graduates: OJT, work immersion, practicum count as valid experience
   - Don't penalize fresh graduates for missing employment certificates

5. AUTO-FAIL:
   - If any mandatory requirement is unverified, set qualified to false and auto_failed to true

RESPONSE FORMAT:
Also include STRENGTHS and WEAKNESSES sections that highlight:
- Strengths: Specific skills, experience, and qualifications they HAVE that match the job
- Weaknesses: What they're missing or what's unverified

Respond ONLY with a JSON object, no extra text, no markdown:
{{
  "qualified": true or false,
  "auto_failed": true or false,
  "score": a number from 0 to 100,
  "reason": "two to three sentences explaining decision",
  "strengths": ["list specific strengths matching the job", "e.g. Strong customer service background", "e.g. Relevant IT technical skills"],
  "weaknesses": ["list specific weaknesses or gaps", "e.g. Education not verified", "e.g. Missing employment certificate"],
  "missing": ["list missing or unverified mandatory requirements"],
  "verified": ["list what was verified from documents"],
  "unverified_claims": ["list education or training claims that could not be verified"]
}}
"""
    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3
    )
    text = response.choices[0].message.content.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    result = json.loads(text.strip())
    return result