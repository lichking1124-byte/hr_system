"""
HR's official required documents, copied from the Online Job Application Form.
Check these lists against HR's paper. Edit a name here and the change appears
in the posting form for the next posting.

A name ending in "(if applicable)" is OPTIONAL for applicants: they are not
forced to upload it. Every other document is required.
"""

ACADEMIC = [
    "Application Letter",
    "Resume",
    "Personal Data Sheet (PDS)",
    "Work Experience Sheet",
    "Transcript of Records",
    "Diploma",
    "Certificate of Eligibility",
    "Certificate of Employment (if applicable)",
    "Office Order for Faculty with Designation (if applicable)",
    "Certificates of Training/Seminars",
    "IPCR / Performance Evaluation (if applicable)",
    "Proof of Professional Development Achievement and Honors (if applicable)",
    "Proof of Membership in Professional Organizations (if applicable)",
    "Proof of Services as Trainor or Lecturer (if applicable)",
    "Proof of Expert Services as Thesis Adviser (if applicable)",
    "Certificate of Scholarship or Fellowship Award (if applicable)",
    "Published Books and Research (if applicable)",
]

NON_ACADEMIC = [
    "Application Letter",
    "Resume",
    "Personal Data Sheet (PDS)",
    "Work Experience Sheet",
    "Performance Evaluation / IPCR (if applicable)",
    "Certificate of Eligibility",
    "Transcript of Records",
    "Diploma",
    "Certificate of Employment / Service Record (if applicable)",
    "Certificates of Training/Seminars",
]

PRESETS = {'academic': ACADEMIC, 'non_academic': NON_ACADEMIC}

# Short guidance shown under each upload box, taken from HR's form.
DOC_HINTS = {
    "Application Letter": "Addressed to the University President",
    "Personal Data Sheet (PDS)": "CS Form 212, Revised 2025",
    "Transcript of Records": "Bachelor's, Master's and/or Doctorate. If you are still completing units, upload a Certificate of Grades",
    "Diploma": "Bachelor's, Master's and/or Doctorate",
    "Certificate of Eligibility": "RA 1080 (Board/Bar), Under Special Laws, CES or CSEE",
    "Performance Evaluation / IPCR (if applicable)": "Rating in the last rating period",
    "Proof of Professional Development Achievement and Honors (if applicable)": "Discoveries, patented inventions, innovations and other creative works",
    "Proof of Membership in Professional Organizations (if applicable)": "Professional organizations and honor societies, and honors received",
    "Proof of Services as Trainor or Lecturer (if applicable)": "Trainor, coordinator, lecturer, resource person or guest speaker",
    "Proof of Expert Services as Thesis Adviser (if applicable)": "Adviser in doctoral dissertations, master's and undergraduate theses",
    "Published Books and Research (if applicable)": "Published or presented in CHED-accredited refereed journals or sites",
}

VALID_TYPES = set(PRESETS)
