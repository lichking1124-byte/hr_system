"""
Fixed rules that decide whether an applicant is qualified.

The AI model never decides. It only answers, for each requirement, "do the
documents show this?" and quotes the exact words it relied on. This file
then checks the quote really exists in the uploaded documents and applies
the rules below. Same inputs always give the same result.

RULES
1. Only requirements the posting actually lists are checked
   (education, experience, skills, other).
2. A requirement counts as VERIFIED only if the AI says it is met AND its
   quote is found, word for word, in one of the applicant's documents.
3. Education marked mandatory must be proven by the Diploma / Transcript
   slot, not by a resume.
4. If any MANDATORY requirement is not verified -> auto-fail.
5. If the posting has NO mandatory requirement, every listed requirement
   must be verified to qualify.  (Change NO_MANDATORY_NEEDS_ALL if HR wants
   different behaviour.)
6. Non-mandatory requirements that are not verified never fail the
   applicant; they are shown to HR as "not verified".
7. Documents that do not look like the slot they were uploaded in are
   flagged for HR (they never change the result by themselves).
"""
import re

NO_MANDATORY_NEEDS_ALL = True

CATEGORIES = [
    ('education', 'Education'),
    ('experience', 'Experience'),
    ('skills', 'Skills'),
    ('other', 'Other requirements'),
]

EMPTY_VALUES = {'', 'none', 'n/a', 'na', 'not applicable', '-'}

DIPLOMA_SLOT = re.compile(r'diploma|transcript|\btor\b', re.I)
RESUME_SLOT = re.compile(r'resume|resumé|curriculum vitae|\bcv\b', re.I)

DIPLOMA_MARKERS = ['diploma', 'conferred', 'transcript of records',
                   'general weighted average', 'this is to certify', 'confers']
RESUME_MARKERS = ['curriculum vitae', 'resume', 'work experience',
                  'objective', 'skills', 'references', 'employment history']


def _squash(text):
    """lowercase, letters and digits only, single spaces"""
    return re.sub(r'[^a-z0-9]+', ' ', (text or '').lower()).strip()


def find_evidence(evidence, documents_text):
    """Return the slot name whose document contains the quote, else None."""
    ev = _squash(evidence)
    if len(ev) < 8:
        return None
    for slot, text in documents_text.items():
        if ev in _squash(text):
            return slot
    return None


def check_document_slots(documents_text):
    """Plain keyword checks. Returns a list of warnings for HR."""
    warnings = []
    for slot, text in documents_text.items():
        low = (text or '').lower()
        if len(low.strip()) < 20:
            warnings.append(
                f'Document check: the file(s) in "{slot}" have no readable text '
                f'(possibly a scan or photo). HR should open and review them.')
            continue
        if DIPLOMA_SLOT.search(slot) and not any(m in low for m in DIPLOMA_MARKERS):
            warnings.append(
                f'Document check: the file in "{slot}" does not look like a '
                f'diploma or transcript. It may have been uploaded in the wrong slot.')
        elif RESUME_SLOT.search(slot) and not any(m in low for m in RESUME_MARKERS):
            warnings.append(
                f'Document check: the file in "{slot}" does not look like a '
                f'resume. It may have been uploaded in the wrong slot.')
    return warnings


def _listed(job_specs, key):
    value = job_specs.get(key if key != 'other' else 'other_requirements') or ''
    return value.strip().lower() not in EMPTY_VALUES, value.strip()


def decide(job_specs, extraction, documents_text):
    mandatory = set(job_specs.get('mandatory_requirements') or [])
    ai_items = (extraction or {}).get('requirements') or {}

    checked = []      # (key, label, required_text, is_mandatory, verified, detail)
    for key, label in CATEGORIES:
        is_listed, required_text = _listed(job_specs, key)
        if not is_listed:
            continue
        item = ai_items.get(key) or {}
        claimed = item.get('met') is True
        slot = find_evidence(item.get('evidence', ''), documents_text) if claimed else None
        verified = claimed and slot is not None
        detail = ''
        if verified and key == 'education' and key in mandatory and not DIPLOMA_SLOT.search(slot):
            verified = False
            detail = f'the proof was found in "{slot}", not in a diploma or transcript'
        elif verified:
            detail = f'found in "{slot}"'
        elif claimed:
            detail = 'the AI\'s quote was not found in any uploaded document'
        else:
            detail = 'no supporting evidence found in the documents'
        checked.append((key, label, required_text, key in mandatory, verified, detail,
                        (item.get('evidence') or '').strip()))

    strengths, weaknesses, auto_fail = [], [], []
    for key, label, required, is_mand, verified, detail, evidence in checked:
        tag = 'mandatory' if is_mand else 'optional'
        if verified:
            quote = f' — "{evidence[:120]}"' if evidence else ''
            strengths.append(f'{label} ({tag}) verified: {required}{quote} [{detail}]')
        else:
            weaknesses.append(f'{label} ({tag}) not verified: {required} [{detail}]')
            if is_mand:
                auto_fail.append(f'Auto-failed: mandatory {label.lower()} requirement not verified ({required})')

    weaknesses.extend(check_document_slots(documents_text))

    if mandatory and any(c[3] for c in checked):
        qualified = not auto_fail
    elif NO_MANDATORY_NEEDS_ALL:
        qualified = bool(checked) and all(c[4] for c in checked)
    else:
        qualified = True

    met = sum(1 for c in checked if c[4])
    total = len(checked)
    if auto_fail:
        reason = (f'Unqualified: {len(auto_fail)} mandatory requirement(s) could not be '
                  f'verified from the submitted documents. {met} of {total} listed '
                  f'requirements verified.')
    elif qualified:
        reason = (f'Qualified: all mandatory requirements verified. {met} of {total} '
                  f'listed requirements verified.')
    else:
        reason = (f'Unqualified: {met} of {total} listed requirements verified and the '
                  f'posting has no mandatory requirements set.')
    unreadable = [slot for slot, text in documents_text.items() if len((text or '').strip()) < 20]
    if unreadable:
        reason += (f' Note: {len(unreadable)} uploaded document(s) had no readable text, so this '
                   f'result may be incomplete. HR should review those files manually.')
    reason += ' Decision made by fixed rules; the AI only read the documents.'

    return {
        'qualified': qualified,
        'met_count': met,
        'total': total,
        'reason': reason,
        'strengths': strengths,
        'weaknesses': weaknesses,
        'auto_fail_reasons': auto_fail,
    }
