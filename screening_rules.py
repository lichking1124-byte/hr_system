"""
Fixed rules that sort every screened applicant into one of three groups.

The AI model never decides. It only answers, for each requirement, "do the
documents show this?" and quotes the exact words it relied on. This file
checks the quote really exists in the uploaded documents and applies the
rules below. Same inputs always give the same result.

A requirement is VERIFIED only if the AI says it is met AND its quote is
found, word for word, in one of the applicant's documents. Education marked
mandatory must be proven by the Diploma / Transcript slot, not a resume.

THE THREE GROUPS
QUALIFIED     Every requirement listed in the posting is verified, and every
              uploaded document is readable and looks like its slot.
DISQUALIFIED  The documents were readable, but a MANDATORY requirement is not
              verified.
UNDER REVIEW  Everything else: a document could not be read clearly (scan,
              photo, damaged file) or looks like it is in the wrong slot, or
              all mandatory requirements are verified but some other
              requirement is not, or the posting lists nothing to check.
              HR checks these by hand.
"""
import re

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


WINDOW_SLACK = 12   # extra words allowed between the first and last word of a quote


def _ordered_within_window(ev_tokens, doc_tokens, slack=WINDOW_SLACK):
    """
    True if every word of the quote appears in the document, in the same order,
    close together. This handles table cells (like the PDS) whose text wraps onto
    a second line, which pushes the neighbouring row's words into the middle of it.
    Words that are scattered far apart, or in a different order, do not match.
    """
    n = len(ev_tokens)
    content = [t for t in ev_tokens if len(t) >= 4]
    if n < 3 or len(content) < 2:
        return False
    for start, tok in enumerate(doc_tokens):
        if tok != ev_tokens[0]:
            continue
        limit = start + n + slack
        pos = start
        try:
            for t in ev_tokens[1:]:
                pos = doc_tokens.index(t, pos + 1, limit)
        except ValueError:
            continue
        return True
    return False


def find_evidence(evidence, documents_text):
    """Return the slot name whose document contains the quote, else None."""
    ev = _squash(evidence)
    if len(ev) < 8:
        return None
    squashed = {slot: _squash(text) for slot, text in documents_text.items()}
    for slot, text in squashed.items():           # exact match first
        if ev in text:
            return slot
    ev_tokens = ev.split()
    for slot, text in squashed.items():           # then the wrapped-cell match
        if _ordered_within_window(ev_tokens, text.split()):
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


def _quotes(item):
    """The AI may return one quote (text) or a list of quotes."""
    ev = item.get('evidence', '')
    if isinstance(ev, str):
        ev = [ev]
    return [str(q).strip() for q in (ev or []) if str(q).strip()]


def _first_match(quotes, documents):
    """(quote, slot) for the first quote found in the given documents, else (None, None)."""
    for q in quotes:
        slot = find_evidence(q, documents)
        if slot:
            return q, slot
    return None, None


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
        quotes = _quotes(item)
        used_quote, slot = _first_match(quotes, documents_text) if claimed else (None, None)
        verified = claimed and slot is not None
        detail = ''
        if claimed and key == 'education' and key in mandatory:
            # Mandatory education must be proven by the diploma or transcript. Look there
            # first, even when the PDS or resume contains the same words.
            proof_docs = {s_: t_ for s_, t_ in documents_text.items() if DIPLOMA_SLOT.search(s_)}
            proof_quote, proof_slot = _first_match(quotes, proof_docs)
            if proof_slot:
                used_quote, slot, verified = proof_quote, proof_slot, True
                detail = f'found in "{proof_slot}"'
            elif slot:
                verified = False
                detail = f'the proof was found in "{slot}", not in a diploma or transcript'
        if not detail:
            if verified:
                detail = f'found in "{slot}"'
            elif claimed:
                shown = '" / "'.join(q[:100] for q in quotes[:2])
                detail = f'the AI\'s quote was not found in any uploaded document: "{shown}"'
            else:
                detail = 'no supporting evidence found in the documents'
        checked.append((key, label, required_text, key in mandatory, verified, detail,
                        (used_quote or '').strip()))

    strengths, weaknesses, auto_fail = [], [], []
    for key, label, required, is_mand, verified, detail, evidence in checked:
        tag = 'mandatory' if is_mand else 'optional'
        if verified:
            quote = f' — "{evidence[:120]}"' if evidence else ''
            strengths.append(f'{label} ({tag}) verified: {required}{quote} [{detail}]')
        else:
            weaknesses.append(f'{label} ({tag}) not verified: {required} [{detail}]')
            if is_mand:
                auto_fail.append(f'Disqualified: mandatory {label.lower()} requirement not verified ({required})')

    doc_warnings = check_document_slots(documents_text)
    weaknesses.extend(doc_warnings)

    met = sum(1 for c in checked if c[4])
    total = len(checked)
    has_mandatory = any(c[3] for c in checked)
    doc_problem = bool(doc_warnings)

    if total == 0:
        result = 'under_review'
        reason = ('Under review: this posting lists no requirements for the system to check, '
                  'so HR should review the documents.')
    elif met == total and not doc_problem:
        result = 'qualified'
        reason = f'Qualified: all {total} listed requirement(s) were verified from the submitted documents.'
    elif auto_fail and not doc_problem:
        result = 'unqualified'
        reason = (f'Disqualified: {len(auto_fail)} mandatory requirement(s) could not be verified from '
                  f'the submitted documents. {met} of {total} listed requirements verified.')
    else:
        result = 'under_review'
        if doc_problem:
            reason = ('Under review: some documents could not be read clearly or may be in the wrong '
                      'slot, so the system cannot confirm every requirement. HR should check those files.')
        elif has_mandatory:
            reason = (f'Under review: all mandatory requirements were verified, but {total - met} other '
                      f'requirement(s) could not be verified.')
        else:
            reason = (f'Under review: {total - met} of {total} requirement(s) could not be verified and '
                      f'none is marked mandatory.')
    reason += ' Result set by fixed rules; the AI only read the documents.'

    return {
        'result': result,
        'qualified': result == 'qualified',
        'met_count': met,
        'total': total,
        'reason': reason,
        'strengths': strengths,
        'weaknesses': weaknesses,
        'auto_fail_reasons': auto_fail if result == 'unqualified' else [],
    }
