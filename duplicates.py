"""
Finds applications that probably come from the same person. Plain code, no AI, no randomness.

Two applications are linked when they share:
  * the same EMAIL (strong signal)       "Juan.Dela.Cruz+jobs@gmail.com" = "juandelacruz@gmail.com"
  * the same PHONE (strong signal)       "0917 123 4567" = "+63 917 123 4567"
  * a SIMILAR NAME (weaker signal)       "Dela Cruz, Juan" = "Juan S. Dela Cruz Jr."

A link is only information for HR. It never changes a screening result, and applying to several
positions is allowed. The one thing the system blocks is the same email applying to the SAME
position twice (see same_email).
"""
import difflib
import re
import unicodedata

NAME_NOISE = {'jr', 'sr', 'ii', 'iii', 'iv', 'mr', 'ms', 'mrs', 'dr', 'engr', 'atty'}
NAME_EXPANSIONS = {'ma': 'maria'}          # "Ma. Cristina" is the usual short form of "Maria Cristina"
NAME_SIMILARITY = 0.90                      # how alike two spellings must be to count as a typo of each other


def normalize_email(email):
    e = (email or '').strip().lower()
    if '@' not in e:
        return e
    local, domain = e.rsplit('@', 1)
    if domain in ('gmail.com', 'googlemail.com'):      # Gmail ignores dots and anything after a plus sign
        local = local.split('+', 1)[0].replace('.', '')
        domain = 'gmail.com'
    return f'{local}@{domain}'


def normalize_phone(phone):
    """The last 10 digits, so 09171234567, +63 917 123 4567 and 0917-123-4567 are all the same.
    Short numbers (7 to 9 digits) are compared as they are. Anything shorter is ignored."""
    digits = re.sub(r'\D', '', phone or '')
    if len(digits) >= 10:
        return digits[-10:]
    return digits if len(digits) >= 7 else ''


def normalize_name(name):
    """The name as a list of lowercase words: no accents, no initials, no Jr./Sr., 'Ma.' -> 'maria'."""
    text = unicodedata.normalize('NFKD', name or '')
    text = ''.join(ch for ch in text if not unicodedata.combining(ch)).lower()
    words = []
    for w in re.findall(r'[a-z]+', text):
        if len(w) == 1 or w in NAME_NOISE:
            continue
        words.append(NAME_EXPANSIONS.get(w, w))
    return words


def names_match(a, b):
    """True when two normalized names are the same person written differently."""
    if not a or not b:
        return False
    sa, sb = set(a), set(b)
    if sa == sb:
        return True                                    # same words in any order ("Dela Cruz, Juan")
    small, big = (sa, sb) if len(sa) <= len(sb) else (sb, sa)
    if len(small) >= 2 and small <= big:
        return True                                    # one of them also writes a middle name
    if min(len(sa), len(sb)) >= 2:                     # a typo: "Jaun" for "Juan"
        ratio = difflib.SequenceMatcher(None, ' '.join(sorted(a)), ' '.join(sorted(b))).ratio()
        return ratio >= NAME_SIMILARITY
    return False


def same_email(a, b):
    ea, eb = normalize_email(a), normalize_email(b)
    return bool(ea) and ea == eb


def compare(x, y):
    """The reasons why two applications look like the same person (empty list = no link).
    x and y are dicts with 'email', 'phone' and 'full_name'."""
    reasons = []
    if same_email(x.get('email'), y.get('email')):
        reasons.append('same email')
    px, py = normalize_phone(x.get('phone')), normalize_phone(y.get('phone'))
    if px and px == py:
        reasons.append('same phone')
    if names_match(normalize_name(x.get('full_name')), normalize_name(y.get('full_name'))):
        reasons.append('similar name')
    return reasons


class PeopleIndex:
    """Lets us find the applications linked to one application without comparing everyone with everyone."""

    def __init__(self, rows):
        self.rows = [dict(r) for r in rows]
        self.by_email, self.by_phone, self.by_token = {}, {}, {}
        for i, r in enumerate(self.rows):
            e, p = normalize_email(r.get('email')), normalize_phone(r.get('phone'))
            if e:
                self.by_email.setdefault(e, set()).add(i)
            if p:
                self.by_phone.setdefault(p, set()).add(i)
            for t in set(normalize_name(r.get('full_name'))):
                self.by_token.setdefault(t, set()).add(i)

    def related(self, row):
        """Every OTHER application linked to this one: [{'other': row, 'reasons': [...], 'strong': bool,
        'same_position': bool}], strongest first."""
        e, p = normalize_email(row.get('email')), normalize_phone(row.get('phone'))
        candidates = set(self.by_email.get(e, ())) | set(self.by_phone.get(p, ()))
        for t in set(normalize_name(row.get('full_name'))):
            candidates |= self.by_token.get(t, set())
        out = []
        for i in sorted(candidates):
            other = self.rows[i]
            if other.get('id') == row.get('id'):
                continue
            reasons = compare(row, other)
            if reasons:
                out.append({'other': other, 'reasons': reasons,
                            'strong': 'same email' in reasons or 'same phone' in reasons,
                            'same_position': other.get('job_id') == row.get('job_id')})
        out.sort(key=lambda m: (not m['strong'], not m['same_position'], m['other'].get('id') or 0))
        return out
