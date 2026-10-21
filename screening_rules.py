"""
Fixed checklist that sorts every screened applicant into one of three groups.

This file is plain code. There is no AI and no randomness in it, so the same
documents and the same posting always give the same result. Re-screening can
never change an answer unless the documents or the posting changed.

THE CHECKS (each one is a yes / no / unclear answer)
1. Required documents  Every required document type has at least one file.
                       ("(if applicable)" documents are optional.)
2. Readable + right    Every uploaded file has readable text and looks like
   slot                its slot (a diploma looks like a diploma, a resume
                       like a resume).
3. Education           The Diploma / Transcript text has degree words AND the
                       main words of a degree the posting names.
4. Skills, Experience, The posting's key terms are searched in the uploaded
   Other               documents. HR sees exactly which terms were missing.

THE THREE GROUPS
QUALIFIED     Every check passes.
DISQUALIFIED  Documents are readable, and a MANDATORY requirement clearly
              fails: education with no matching degree in a readable diploma,
              or a mandatory skills / experience / other category where NONE
              of its key terms appear anywhere.
UNDER REVIEW  Anything unclear: a missing or unreadable file (scan, photo),
              a file in the wrong slot, some terms missing on a requirement
              that is not clear-cut, or a posting with nothing to check.
              HR checks these by hand.
"""
import re

CATEGORIES = [
    ('education', 'Education'),
    ('experience', 'Experience'),
    ('skills', 'Skills'),
    ('other', 'Other requirements'),
]
TERM_CATEGORIES = ('experience', 'skills', 'other')

EMPTY_VALUES = {'', 'none', 'n/a', 'na', 'not applicable', '-'}

DIPLOMA_SLOT = re.compile(r'diploma|transcript|\btor\b', re.I)
RESUME_SLOT = re.compile(r'resume|resumé|curriculum vitae|\bcv\b', re.I)

DIPLOMA_MARKERS = ['diploma', 'conferred', 'transcript of records',
                   'general weighted average', 'this is to certify', 'confers']
RESUME_MARKERS = ['curriculum vitae', 'resume', 'work experience',
                  'objective', 'skills', 'references', 'employment history']

MIN_READABLE_CHARS = 20

# Words that say a document shows a college / graduate degree.
DEGREE_WORDS = {'bachelor', 'bachelors', 'master', 'masters', 'doctor', 'doctorate',
                'associate', 'baccalaureate', 'degree'}

# Filler words ignored when finding the "key terms" of a requirement.
STOPWORDS = {
    'and', 'or', 'of', 'in', 'the', 'a', 'an', 'to', 'for', 'with', 'at', 'on', 'any',
    'related', 'relevant', 'similar', 'must', 'have', 'has', 'be', 'is', 'are', 'as',
    'by', 'from', 'including', 'etc', 'preferably', 'ability', 'knowledge', 'good',
    'basic', 'proficient', 'proficiency', 'familiarity', 'working', 'minimum', 'least',
    'required', 'requirement', 'year', 'years', 'month', 'months', 'experience',
    'skill', 'skills', 'field', 'work', 'one', 'two', 'three', 'five', 'ten',
    'more', 'than', 'least', 'plus',
}
# Extra words ignored for education (the generic parts of a degree title).
EDU_GENERIC = STOPWORDS | {
    'bachelor', 'bachelors', 'master', 'masters', 'doctor', 'doctorate', 'degree',
    'science', 'arts', 'course', 'graduate', 'holder', 'completion', 'bs', 'ba', 'ms',
    'ma', 'college', 'university', 'associate', 'units', 'level', 'equivalent',
    'program', 'major', 'post', 'studies', 'diploma', 'certificate', 'baccalaureate',
    'completed', 'candidate',
}


# ---------------------------------------------------------------- text helpers
def _stem(word):
    """Very small, readable stemmer so networks / networking / network match."""
    w = word
    if w.endswith('ies') and len(w) > 4:
        w = w[:-3] + 'y'
    elif w.endswith('s') and not w.endswith('ss') and len(w) > 3:
        w = w[:-1]
    if len(w) > 6 and w.endswith('ing'):
        w = w[:-3]
    elif len(w) > 5 and w.endswith('ed'):
        w = w[:-2]
    if len(w) > 4 and w.endswith('e'):
        w = w[:-1]
    return w


def _tokens(text):
    """lowercase words (letters and digits only), in order"""
    return re.findall(r'[a-z0-9]+', (text or '').lower())


def _stems(text):
    return [_stem(t) for t in _tokens(text)]


def _key_stems(phrase, ignore):
    """The main words of a phrase, as stems. Drops filler words and bare numbers."""
    out = []
    for t in _tokens(phrase):
        if t in ignore or t.isdigit() or len(t) < 2:
            continue
        s = _stem(t)
        if s not in out:
            out.append(s)
    return out


def _split_phrases(text):
    """Split a posting field into separate terms at commas, semicolons, slashes,
    new lines, brackets and the word 'and'."""
    parts = re.split(r'[,;/\n\r•()]|\band\b', text or '', flags=re.I)
    return [p.strip() for p in parts if p and p.strip()]


def _present(term_stems, doc_stems, slack=8):
    """True if all the term's main words appear close together (within the term
    length + slack words) in the document. One word: it just has to appear."""
    if not term_stems:
        return False
    need = set(term_stems)
    if len(need) == 1:
        return term_stems[0] in set(doc_stems)
    span = len(term_stems) + slack
    for i, s in enumerate(doc_stems):
        if s in need and need <= set(doc_stems[i:i + span]):
            return True
    return False


def _listed(job_specs, key):
    value = job_specs.get('other_requirements' if key == 'other' else key) or ''
    return value.strip().lower() not in EMPTY_VALUES, value.strip()


def _readable(text):
    return len((text or '').strip()) >= MIN_READABLE_CHARS


# ------------------------------------------------------------ document checks
def check_required_documents(job_specs, documents_text):
    """Return the list of required document types that have no uploaded file."""
    uploaded = {s.strip().lower() for s in documents_text}
    missing = []
    for doc in job_specs.get('required_documents') or []:
        if doc.strip().lower().endswith('(if applicable)'):
            continue
        if doc.strip().lower() not in uploaded:
            missing.append(doc)
    return missing


def check_document_slots(documents_text):
    """Plain keyword checks. Returns a list of warnings for HR."""
    warnings = []
    for slot, text in documents_text.items():
        low = (text or '').lower()
        if not _readable(low):
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


# ------------------------------------------------------------------ the checks
def _check_education(required_text, documents_text):
    """
    Returns (status, detail). status is 'pass', 'fail' (clear failure in a readable
    diploma) or 'unclear' (no diploma / transcript to look at, or it cannot be read).
    """
    proof = {s: t for s, t in documents_text.items() if DIPLOMA_SLOT.search(s)}
    if not proof:
        return 'unclear', 'no Diploma / Transcript file was uploaded to check against'
    readable = {s: t for s, t in proof.items() if _readable(t)}
    if not readable:
        return 'unclear', 'the Diploma / Transcript has no readable text (scan or photo)'

    posting_words = set(_tokens(required_text))
    needs_degree_words = bool(posting_words & DEGREE_WORDS)

    # alternatives: "BSIT, BS Computer Science or any related course"
    alternatives = []
    for alt in re.split(r'[,;\n\r/]|\bor\b', required_text, flags=re.I):
        key = _key_stems(alt, EDU_GENERIC)
        if key:
            alternatives.append((alt.strip(), key))

    for slot, text in readable.items():
        doc_tokens = set(_tokens(text))
        doc_stems = _stems(text)
        has_degree_words = bool(doc_tokens & DEGREE_WORDS)
        if needs_degree_words and not has_degree_words:
            continue
        if not alternatives:
            return 'pass', f'degree words found in "{slot}"'
        for alt_text, key in alternatives:
            if _present(key, doc_stems):
                return 'pass', f'found in "{slot}": {" ".join(key)}'

    names = ' / '.join(' '.join(k) for _, k in alternatives) or 'a degree'
    looks_ok = any(any(m in t.lower() for m in DIPLOMA_MARKERS) for t in readable.values())
    if not looks_ok:
        return 'unclear', 'the Diploma / Transcript does not look like one, so it cannot be judged'
    if needs_degree_words and not any(set(_tokens(t)) & DEGREE_WORDS for t in readable.values()):
        return 'fail', 'the readable Diploma / Transcript shows no degree words (bachelor, master, etc.)'
    return 'fail', f'the readable Diploma / Transcript does not contain the main words of the required degree ({names})'


def _check_terms(required_text, documents_text):
    """
    Returns (status, found_terms, missing_terms).
    status: 'pass' (all found), 'none' (none found), 'partial', or 'noterms'.
    """
    terms = []
    for p in _split_phrases(required_text):
        key = _key_stems(p, STOPWORDS)
        if key and (p, key) not in terms:
            terms.append((p, key))
    if not terms:
        return 'noterms', [], []
    # search each document on its own so words from two files are never glued together
    per_doc = [_stems(t) for t in documents_text.values()]
    found, missing = [], []
    for phrase, key in terms:
        if any(_present(key, d) for d in per_doc):
            found.append(phrase)
        else:
            missing.append(phrase)
    if not missing:
        return 'pass', found, missing
    if not found:
        return 'none', found, missing
    return 'partial', found, missing


# ---------------------------------------------------------------- the decision
def decide(job_specs, documents_text):
    mandatory = set(job_specs.get('mandatory_requirements') or [])

    strengths, weaknesses, auto_fail = [], [], []
    met = total = 0
    unclear = False

    # 1 + 2: documents
    missing_docs = check_required_documents(job_specs, documents_text)
    if missing_docs:
        unclear = True
        weaknesses.append('Required documents missing: ' + ', '.join(missing_docs))
    elif job_specs.get('required_documents'):
        strengths.append('Required documents: all uploaded')
    doc_warnings = check_document_slots(documents_text)
    weaknesses.extend(doc_warnings)
    doc_problem = bool(doc_warnings) or bool(missing_docs)
    if documents_text and not doc_warnings:
        strengths.append('Documents: every file is readable and looks like its slot')

    # 3 + 4: requirements
    for key, label in CATEGORIES:
        is_listed, required_text = _listed(job_specs, key)
        if not is_listed:
            continue
        total += 1
        is_mand = key in mandatory
        tag = 'mandatory' if is_mand else 'optional'

        if key == 'education':
            status, detail = _check_education(required_text, documents_text)
            if status == 'pass':
                met += 1
                strengths.append(f'{label} ({tag}) verified: {required_text} [{detail}]')
            else:
                weaknesses.append(f'{label} ({tag}) not verified: {required_text} [{detail}]')
                if status == 'fail' and is_mand:
                    auto_fail.append(f'Disqualified: mandatory education requirement not met ({required_text})')
                else:
                    unclear = True
        else:
            status, found, missing = _check_terms(required_text, documents_text)
            if status == 'pass':
                met += 1
                strengths.append(f'{label} ({tag}) verified: found {", ".join(found)}')
            elif status == 'noterms':
                unclear = True
                weaknesses.append(f'{label} ({tag}) could not be checked by the system: {required_text} '
                                  f'[no key terms could be taken from it. HR should check by hand]')
            else:
                line = f'{label} ({tag}) not verified: missing {", ".join(missing)}'
                if found:
                    line += f' [found: {", ".join(found)}]'
                weaknesses.append(line)
                if status == 'none' and is_mand and not doc_problem:
                    auto_fail.append(f'Disqualified: mandatory {label.lower()} requirement not met '
                                     f'(none of these found: {", ".join(missing)})')
                else:
                    unclear = True

    if total == 0:
        result = 'under_review'
        reason = ('Under review: this posting lists no requirements for the system to check, '
                  'so HR should review the documents.')
    elif met == total and not doc_problem:
        result = 'qualified'
        reason = f'Qualified: all {total} listed requirement(s) were found in the submitted documents.'
    elif auto_fail and not doc_problem:
        result = 'unqualified'
        reason = (f'Disqualified: {len(auto_fail)} mandatory requirement(s) clearly not met in the '
                  f'readable documents. {met} of {total} listed requirements verified.')
    else:
        result = 'under_review'
        if missing_docs:
            reason = 'Under review: some required documents are missing. HR should check.'
        elif doc_warnings:
            reason = ('Under review: some documents could not be read clearly or may be in the wrong '
                      'slot, so the system cannot confirm every requirement. HR should check those files.')
        else:
            reason = (f'Under review: {total - met} of {total} requirement(s) could not be fully '
                      f'verified. See the checklist for the exact missing items.')
    reason += ' Result set by a fixed checklist (plain code, no AI).'

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
