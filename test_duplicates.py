"""Tests for duplicates.py, including the 100-case benchmark described in Chapter 3.7.

The 100 test submissions are SYNTHETIC (made-up people) and are built with a fixed seed, so the numbers
are the same every run. Each pair has a known right answer: "same person" or "different people"."""
import random
import unittest

from duplicates import (PeopleIndex, compare, names_match, normalize_name,
                        normalize_phone, same_email)

FIRST = ['Juan', 'Maria', 'Jose', 'Ana', 'Pedro', 'Luz', 'Carlo', 'Rina', 'Mark', 'Joy', 'Paolo', 'Grace', 'Ramon',
         'Liza', 'Noel', 'Cathy', 'Dante', 'Mae', 'Rico', 'Tess', 'Arnel', 'Bea', 'Nico', 'Hazel', 'Gino', 'Lea',
         'Mario', 'Faith', 'Edgar', 'Joan', 'Lito', 'Rose', 'Allan', 'Dina', 'Ruel', 'Mia', 'Oscar', 'Vina', 'Cesar', 'Ella']
LAST = ['Dela Cruz', 'Santos', 'Reyes', 'Garcia', 'Mendoza', 'Bautista', 'Ramos', 'Aquino', 'Villanueva', 'Castillo',
        'Navarro', 'Pascual', 'Soriano', 'Manalo', 'Tolentino', 'Dizon', 'Cruz', 'Salvador', 'Valdez', 'Lacson',
        'Mercado', 'Alonzo', 'Cabrera', 'Domingo', 'Estrada', 'Fajardo', 'Gonzales', 'Hernandez', 'Ibarra', 'Jacinto']


def build_benchmark(seed=2026):
    """Returns 100 (application_a, application_b, same_person, category) test submissions."""
    rnd = random.Random(seed)
    names = [(f, l) for f in FIRST for l in LAST]
    rnd.shuffle(names)
    counter = {'n': 0}

    def person():
        f, l = names.pop()
        counter['n'] += 1
        n = counter['n']
        return {'first': f, 'last': l, 'email': f'{f.lower()}.{l.lower().replace(" ", "")}{n}@mail.test',
                'phone': f'09{17 + n % 9}{1000000 + n * 7919 % 9000000:07d}'[:11], 'job_id': rnd.choice([1, 2, 3])}

    def app(p, **over):
        a = {'id': None, 'job_id': p['job_id'], 'full_name': f"{p['first']} {p['last']}", 'email': p['email'], 'phone': p['phone']}
        a.update(over)
        return a

    def other_job(p):
        return next(j for j in (1, 2, 3) if j != p['job_id'])

    def phone_formats(phone):
        d = phone[-10:]
        return [f'0{d}', f'+63 {d[:3]} {d[3:6]} {d[6:]}', f'0{d[:3]}-{d[3:6]}-{d[6:]}', f'63{d}']

    def name_variants(p):
        f, l = p['first'], p['last']
        return [f'{l}, {f}', f'{f} {l.split()[-1]}'.upper() if len(l.split()) == 1 else f'{f} {l}'.upper(), f'{f} S. {l}',
                f'{f} {l} Jr.', f'{l} {f}']

    pairs = []
    for _ in range(20):                                    # A. exact repeats
        p = person(); pairs.append((app(p), app(p), True, 'exact repeat'))
    for _ in range(15):                                    # B. same email, name written differently, other phone
        p = person(); pairs.append((app(p), app(p, full_name=rnd.choice(name_variants(p)), phone=f'09{rnd.randint(10**8, 10**9 - 1)}'),
                                    True, 'same email, name written differently'))
    for _ in range(15):                                    # C. same phone in another format, other email
        p = person(); pairs.append((app(p), app(p, phone=rnd.choice(phone_formats(p['phone'])[1:]),
                                               email=f'other{rnd.randint(1000, 9999)}@mail.test'), True, 'same phone, other format'))
    for _ in range(10):                                    # D. only the name is alike (typo, Ma., middle name)
        p = person()
        f, l = p['first'], p['last']
        typo = f[1] + f[0] + f[2:] if len(f) > 3 else f
        variant = rnd.choice([f'{typo} {l}', f'{f} Santos {l}', f'{f} {l} Jr.', f'{l}, {f} M.'])
        pairs.append((app(p), app(p, full_name=variant, email=f'new{rnd.randint(1000, 9999)}@mail.test',
                                   phone=f'09{rnd.randint(10**8, 10**9 - 1)}'), True, 'name only (typo or middle name)'))
    for _ in range(10):                                    # E. same person, another position or department
        p = person(); pairs.append((app(p), app(p, job_id=other_job(p)), True, 'applied to another position'))
    for _ in range(25):                                    # F. two different people
        p, q = person(), person(); pairs.append((app(p), app(q), False, 'different people'))
    for _ in range(3):                                     # G1. family members sharing the household phone
        p = person(); q = person(); q['last'] = p['last']
        pairs.append((app(p), app(q, phone=p['phone']), False, 'family sharing one phone'))
    for _ in range(2):                                     # G2. different people with look-alike names
        p = person(); look = p['first'] + 'a'
        pairs.append((app(p), app(person(), full_name=f"{look} {p['last']}"), False, 'look-alike names'))
    assert len(pairs) == 100
    return pairs


def evaluate(pairs, strong_only=False):
    tp = fp = fn = tn = 0
    misses = []
    for a, b, truth, category in pairs:
        reasons = compare(a, b)
        flagged = any(r in ('same email', 'same phone') for r in reasons) if strong_only else bool(reasons)
        if flagged and truth: tp += 1
        elif flagged and not truth: fp += 1; misses.append(('false alarm', category))
        elif not flagged and truth: fn += 1; misses.append(('missed', category))
        else: tn += 1
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    return dict(tp=tp, fp=fp, fn=fn, tn=tn, precision=precision, recall=recall, misses=misses)


class Normalizers(unittest.TestCase):
    def test_emails(self):
        self.assertTrue(same_email('Juan.Dela.Cruz+jobs@Gmail.com', 'juandelacruz@gmail.com'))
        self.assertFalse(same_email('a.b@school.edu.ph', 'ab@school.edu.ph'))      # dots only ignored for Gmail
        self.assertFalse(same_email('', ''))

    def test_phones(self):
        self.assertEqual(normalize_phone('0917 123 4567'), normalize_phone('+63 917 123 4567'))
        self.assertEqual(normalize_phone('0917-123-4567'), normalize_phone('09171234567'))
        self.assertEqual(normalize_phone('12345'), '')                               # too short to mean anything
        self.assertEqual(normalize_phone(None), '')

    def test_names(self):
        n = normalize_name
        self.assertTrue(names_match(n('Dela Cruz, Juan'), n('Juan Dela Cruz')))
        self.assertTrue(names_match(n('Juan S. Dela Cruz Jr.'), n('JUAN DELA CRUZ')))
        self.assertTrue(names_match(n('Ma. Cristina Reyes'), n('Maria Cristina Reyes')))
        self.assertTrue(names_match(n('Jaun Dela Cruz'), n('Juan Dela Cruz')))
        self.assertTrue(names_match(n('José Peña'), n('Jose Pena')))
        self.assertFalse(names_match(n('Juan Dela Cruz'), n('Pedro Santos')))
        self.assertFalse(names_match(n('Maria Santos'), n('Maria Reyes')))           # one shared word is not enough
        self.assertFalse(names_match([], n('Juan Dela Cruz')))


class Index(unittest.TestCase):
    ROWS = [
        {'id': 1, 'job_id': 1, 'full_name': 'Juan Dela Cruz', 'email': 'juan@x.com', 'phone': '0917 111 2222'},
        {'id': 2, 'job_id': 2, 'full_name': 'Dela Cruz, Juan', 'email': 'other@x.com', 'phone': '+63 917 111 2222'},
        {'id': 3, 'job_id': 1, 'full_name': 'Pedro Santos', 'email': 'pedro@x.com', 'phone': '0918 333 4444'},
        {'id': 4, 'job_id': 1, 'full_name': 'Juan Dela Cruz', 'email': 'JUAN@x.com', 'phone': ''},
    ]

    def test_finds_the_same_person_in_other_positions_and_in_the_same_position(self):
        idx = PeopleIndex(self.ROWS)
        found = {m['other']['id']: m for m in idx.related(self.ROWS[0])}
        self.assertEqual(sorted(found), [2, 4])
        self.assertTrue(found[4]['same_position'] and 'same email' in found[4]['reasons'])
        self.assertFalse(found[2]['same_position'])
        self.assertIn('same phone', found[2]['reasons'])
        self.assertEqual(idx.related(self.ROWS[2]), [])                                # Pedro is alone

    def test_an_application_is_never_linked_to_itself(self):
        idx = PeopleIndex(self.ROWS)
        self.assertNotIn(1, [m['other']['id'] for m in idx.related(self.ROWS[0])])

    def test_strongest_links_come_first(self):
        rows = [{'id': 1, 'job_id': 1, 'full_name': 'Ana Reyes', 'email': 'a@x.com', 'phone': ''},
                {'id': 2, 'job_id': 2, 'full_name': 'Ana Reyes', 'email': 'zzz@x.com', 'phone': ''},
                {'id': 3, 'job_id': 2, 'full_name': 'Someone', 'email': 'a@x.com', 'phone': ''}]
        found = PeopleIndex(rows).related(rows[0])
        self.assertEqual([m['other']['id'] for m in found], [3, 2])                    # email match before name-only match

    def test_works_on_a_few_hundred_applications_quickly(self):
        import time
        rows = [{'id': i, 'job_id': i % 5, 'full_name': f'{FIRST[i % 40]} {LAST[i % 30]} {i}', 'email': f'p{i}@x.com',
                 'phone': f'0917{i:07d}'} for i in range(600)]
        idx = PeopleIndex(rows)
        t = time.time()
        for r in rows:
            idx.related(r)
        self.assertLess(time.time() - t, 3.0)


class Benchmark(unittest.TestCase):
    """The 100 synthetic test submissions from Chapter 3.7."""

    def test_the_benchmark_has_the_planned_makeup(self):
        pairs = build_benchmark()
        self.assertEqual(len(pairs), 100)
        self.assertEqual(sum(1 for p in pairs if p[2]), 70)         # 70 same-person pairs
        self.assertEqual(sum(1 for p in pairs if not p[2]), 30)     # 30 different-people pairs

    def test_the_benchmark_is_repeatable(self):
        self.assertEqual(build_benchmark(), build_benchmark())

    def test_precision_and_recall(self):
        pairs = build_benchmark()
        all_signals, strong = evaluate(pairs), evaluate(pairs, strong_only=True)
        print('\n--- duplicate matching benchmark (100 synthetic test submissions) ---')
        for label, r in (('email + phone + name', all_signals), ('email + phone only  ', strong)):
            print(f"{label}: precision {r['precision']:.1%}, recall {r['recall']:.1%}  "
                  f"(found {r['tp']}, false alarms {r['fp']}, missed {r['fn']}, correct non-matches {r['tn']})")
        print('false alarms / misses with name check:', all_signals['misses'])
        self.assertGreaterEqual(all_signals['recall'], 0.95)
        self.assertGreaterEqual(all_signals['precision'], 0.85)
        self.assertGreaterEqual(strong['precision'], 0.90)
        self.assertEqual(all_signals['tp'] + all_signals['fn'], 70)


if __name__ == '__main__':
    unittest.main(verbosity=2)
