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
4. Experience         If the posting gives a length ("at least 6 months"), the
                       code reads dated work ranges from the Certificate of
                       Employment, the resume's Work Experience part and the PDS,
                       merges overlaps and adds them up. Too few months = not met.
5. Skills, Other      The posting's key terms are searched in the uploaded
                       documents. HR sees exactly which terms were missing.

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
import calendar
import re
from datetime import date

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
    'more', 'than', 'least', 'plus', 'role', 'roles', 'position', 'job', 'task', 'tasks',
}
# "Willingness to ..." items are the applicant's promise. No document can prove them,
# so the system does not score them. HR confirms them.
DECLARATION = re.compile(r'^\s*(willing|willingness|ready to|open to)\b', re.I)
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
    Returns (status, found_terms, missing_terms, declarations).
    status: 'pass' (all found), 'none' (none found), 'partial', 'noterms',
            or 'declarations_only' (the posting item is only a "willingness" promise).
    A term written as "A or B" is found if A or B is found.
    """
    terms, declarations = [], []
    for p in _split_phrases(required_text):
        if DECLARATION.match(p):
            declarations.append(p)
            continue
        alts = []
        for alt in re.split(r'\bor\b', p, flags=re.I):
            key = _key_stems(alt, STOPWORDS)
            if key:
                alts.append(key)
        if alts and (p, alts) not in terms:
            terms.append((p, alts))
    if not terms:
        return ('declarations_only' if declarations else 'noterms'), [], [], declarations
    # search each document on its own so words from two files are never glued together
    per_doc = [_stems(t) for t in documents_text.values()]
    found, missing = [], []
    for phrase, alts in terms:
        if any(_present(key, d) for key in alts for d in per_doc):
            found.append(phrase)
        else:
            missing.append(phrase)
    if not missing:
        return 'pass', found, missing, declarations
    if not found:
        return 'none', found, missing, declarations
    return 'partial', found, missing, declarations


# ------------------------------------------------------- length of experience
# The system reads date ranges ("July 2024 to Present", "07/01/2024 - 12/31/2024")
# ONLY from work-experience places: a Certificate of Employment, the "Work Experience"
# part of a resume, and section V of the PDS. Education dates, training dates and
# letters are never counted. Overlapping ranges are merged so the same job listed in
# two documents is counted once. "Present" means the date the applicant applied, so
# re-screening later gives the same answer.
DAYS_PER_MONTH = 30.4375
TOLERANCE_MONTHS = 0.15

EMPLOY_SLOT = re.compile(r'employment|service record|work experience|\bcoe\b', re.I)
PDS_SLOT = re.compile(r'personal data sheet|\bpds\b', re.I)
WORK_HEAD = re.compile(
    r'^[ \t]*(?:[ivx]+\.[ \t]*)?(?:work experience|employment history|employment record|'
    r'professional experience|work history)', re.I | re.M)
PDS_END = re.compile(
    r'(?:vi|vii|viii)\.\s*(?:learning and development|voluntary work|other information)|'
    r'signature \(e-signature|cs form (?:no\. )?212 \(revised', re.I)
END_HEAD = re.compile(
    r'^[ \t]*(?:[ivx]+\.[ \t]*)?(?:education|educational background|skills|references|training|'
    r'learning and development|voluntary work|eligibility|civil service|other information|'
    r'objective|awards|achievements|certifications|seminars)\b', re.I | re.M)

_MONTHS = {'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6, 'jul': 7,
           'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12}
_MONTH_RE = (r'jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|'
             r'sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?')
DATE_TOKEN = re.compile(
    r'(?P<name>(?<![a-z])(?P<mon>' + _MONTH_RE + r')\.?\s+(?:(?P<day>\d{1,2})(?:st|nd|rd|th)?,?\s+)?(?P<y1>\d{4}))'
    r'|(?P<num3>(?P<m3>\d{1,2})[/-](?P<d3>\d{1,2})[/-](?P<y3>\d{4}))'
    r'|(?P<num2>(?P<m2>\d{1,2})[/-](?P<y2>\d{4}))'
    r'|(?P<now>\bpresent\b|\bcurrent(?:ly)?\b|\bto date\b|\bnow\b)'
    r'|(?P<year>\b(?:19|20)\d{2}\b)', re.I)
CONNECTOR = re.compile(r'^\s*(?:-|–|—|to|until|up to|till)?\s*$', re.I)

_WORD_NUMBERS = {'a': 1, 'an': 1, 'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6,
                 'seven': 7, 'eight': 8, 'nine': 9, 'ten': 10, 'eleven': 11, 'twelve': 12}
NO_EXPERIENCE = re.compile(r'\bno experience\b|experience (?:is )?not required|\bnone required\b', re.I)


def required_months(text):
    """'At least 6 months' -> 6.0, '2 years' -> 24.0, 'one year' -> 12.0, no duration -> None."""
    m = re.search(r'(\d+(?:\.\d+)?|' + '|'.join(_WORD_NUMBERS) + r')[\s-]*(years?|yrs?|months?|mos?)\b',
                  text or '', re.I)
    if not m:
        return None
    n = m.group(1).lower()
    n = float(n) if n[0].isdigit() else float(_WORD_NUMBERS[n])
    return n * (12.0 if m.group(2).lower().startswith('y') else 1.0)


def _to_date(tok, as_of, is_end):
    kind, y, mo, d = tok
    if kind == 'now':
        return as_of
    if mo is None:
        return None                      # year only: not precise enough
    if d is None:
        d = calendar.monthrange(y, mo)[1] if is_end else 1
    try:
        return date(y, mo, min(d, calendar.monthrange(y, mo)[1]))
    except ValueError:
        return None


def _date_tokens(text):
    out = []
    for m in DATE_TOKEN.finditer(text):
        if m.group('name'):
            out.append((m.start(), m.end(), ('date', int(m.group('y1')), _MONTHS[m.group('mon')[:3].lower()],
                                              int(m.group('day')) if m.group('day') else None)))
        elif m.group('num3'):
            # The PDS (CS Form 212) writes dd/mm/yyyy, so day-first is the default.
            # Only when the second number cannot be a month (like 07/25/2024) is it month-first.
            a, b, y = int(m.group('m3')), int(m.group('d3')), int(m.group('y3'))
            d, mo = (a, b) if (a > 12 or b <= 12) else (b, a)
            if 1 <= mo <= 12 and 1 <= d <= 31:
                out.append((m.start(), m.end(), ('date', y, mo, d)))
        elif m.group('num2'):
            mo, y = int(m.group('m2')), int(m.group('y2'))
            if 1 <= mo <= 12:
                out.append((m.start(), m.end(), ('date', y, mo, None)))
        elif m.group('now'):
            out.append((m.start(), m.end(), ('now', None, None, None)))
        else:
            out.append((m.start(), m.end(), ('date', int(m.group('year')), None, None)))
    return out


def _work_sources(documents_text):
    """[(slot, text)] limited to the places where work experience is written."""
    out = []
    for slot, text in documents_text.items():
        text = text or ''
        if EMPLOY_SLOT.search(slot):
            out.append((slot, text))
        elif PDS_SLOT.search(slot) or RESUME_SLOT.search(slot):
            head = WORK_HEAD.search(text)
            if not head:
                continue
            rest = text[head.end():]
            cuts = [m.start() for m in (END_HEAD.search(rest), PDS_END.search(rest)) if m]
            out.append((slot, rest[:min(cuts)] if cuts else rest))
    return out


def _line_start(text, pos):
    return text.rfind('\n', 0, pos) + 1


def _entry_start(text, pos):
    """Where a job entry begins: the line holding its dates, plus up to two lines just above it
    (a job title written above the dates). Stops at a blank line or at a line that has dates,
    so one row of a table never borrows words from the row above it."""
    start = _line_start(text, pos)
    for _ in range(2):
        if start == 0:
            break
        prev_end = start - 1
        prev_start = _line_start(text, prev_end)
        line = text[prev_start:prev_end]
        if not line.strip() or DATE_TOKEN.search(line):
            break
        start = prev_start
    return start


def measure_experience(documents_text, term_alternatives, as_of):
    """
    Returns (months, ranges, ambiguous). months: merged months of related work.
    ranges: [(start, end, slot)] that were counted. ambiguous: year-only ranges that
    could not be measured. term_alternatives: key-word lists; a range counts only if
    its job entry mentions at least one (empty list = every work range counts).
    """
    counted, ambiguous = [], []
    for slot, text in _work_sources(documents_text):
        toks = _date_tokens(text)
        i = 0
        ranges = []                                   # (tok_a, tok_b, a_start, b_end)
        while i + 1 < len(toks):
            a, b = toks[i], toks[i + 1]
            if a[2][0] != 'now' and CONNECTOR.match(text[a[1]:b[0]]):
                ranges.append((a, b))
                i += 2
            else:
                i += 1
        for n, (a, b) in enumerate(ranges):
            lo = _entry_start(text, a[0])
            hi = min(b[1] + 300, _line_start(text, ranges[n + 1][0][0]) if n + 1 < len(ranges) else len(text))
            context = _stems(text[lo:hi])
            ctx = set(context)          # the job entry only needs to mention the words, not in a tight window
            if term_alternatives and not any(set(k) <= ctx for k in term_alternatives):
                continue
            start = _to_date(a[2], as_of, False)
            end = _to_date(b[2], as_of, True)
            if start is None or end is None:
                ambiguous.append(slot)
                continue
            end = min(end, as_of)
            if end < start:
                continue
            counted.append((start, end, slot))
    counted.sort()
    merged = []
    for st, en, slot in counted:
        if merged and st <= merged[-1][1]:
            if en > merged[-1][1]:
                merged[-1][1] = en
            if slot not in merged[-1][2]:
                merged[-1][2].append(slot)
        else:
            merged.append([st, en, [slot]])
    days = sum((en - st).days + 1 for st, en, _ in merged)
    return days / DAYS_PER_MONTH, merged, ambiguous


def _fmt(d):
    return d.strftime('%b %Y')


def _check_experience(required_text, documents_text, as_of):
    """Returns (status, detail): 'pass', 'fail' (clear) or 'unclear'."""
    need = required_months(required_text)
    alts = []
    for p in _split_phrases(required_text):
        if DECLARATION.match(p):
            continue
        for alt in re.split(r'\bor\b', p, flags=re.I):
            key = _key_stems(alt, STOPWORDS)
            if key:
                alts.append(key)
    months, merged, ambiguous = measure_experience(documents_text, alts, as_of)
    shown = '; '.join(f'{_fmt(st)} to {_fmt(en)} ({" + ".join(sl)})' for st, en, sl in merged)
    if months + TOLERANCE_MONTHS >= need:
        return 'pass', f'{months:.1f} months of related work found, {need:g} needed: {shown}'
    if ambiguous:
        return 'unclear', (f'only {months:.1f} months could be measured, {need:g} needed, and some jobs '
                           f'show years without months, so HR should check the exact dates')
    if merged:
        return 'fail', f'only {months:.1f} months of related work found, {need:g} needed: {shown}'
    return 'fail', f'no dated related work experience found, {need:g} months needed'


# ---------------------------------------------------------------- the decision
def decide(job_specs, documents_text, as_of=None):
    as_of = as_of or date.today()
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
        is_mand = key in mandatory
        tag = 'mandatory' if is_mand else 'optional'

        if key == 'education':
            total += 1
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
            continue

        if key == 'experience':
            if NO_EXPERIENCE.search(required_text):
                continue                      # the posting asks for no experience
            if required_months(required_text) is not None:
                total += 1
                status, detail = _check_experience(required_text, documents_text, as_of)
                if status == 'pass':
                    met += 1
                    strengths.append(f'{label} ({tag}) verified: {required_text} [{detail}]')
                else:
                    weaknesses.append(f'{label} ({tag}) not verified: {required_text} [{detail}]')
                    if status == 'fail' and is_mand and not doc_problem:
                        auto_fail.append(f'Disqualified: mandatory experience requirement not met ({detail})')
                    else:
                        unclear = True
                continue

        status, found, missing, declarations = _check_terms(required_text, documents_text)
        if status == 'declarations_only':
            # a promise, not provable from documents: not scored, HR confirms
            strengths.append(f'{label}: "{required_text}" is the applicant\'s own promise. '
                             f'Documents cannot prove it, so it is not scored. HR confirms it.')
            continue
        total += 1
        if status == 'pass':
            met += 1
            line = f'{label} ({tag}) verified: found {", ".join(found)}'
            strengths.append(line)
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
