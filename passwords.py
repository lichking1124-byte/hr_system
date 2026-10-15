"""Password hashing helpers (bcrypt, salted). No app imports, so database.py can use it."""
import bcrypt

# Precomputed hash used to equalize timing when a username does not exist,
# so response time doesn't reveal which usernames are real.
DUMMY_HASH = "$2b$12$GoCEpsmgpLKRbb3EL9NCDexACEcmEHdjacVq7vJZs.sNSgOCbIOCi"

DEFAULT_SEED_PASSWORD = "bulsu2026"


def _prep(password):
    # bcrypt only uses the first 72 bytes
    return str(password).encode("utf-8")[:72]


def hash_password(password):
    return bcrypt.hashpw(_prep(password), bcrypt.gensalt(rounds=12)).decode("utf-8")


def is_hashed(value):
    return isinstance(value, str) and value.startswith(("$2a$", "$2b$", "$2y$")) and len(value) == 60


def verify_password(password, stored):
    """True if `password` matches `stored`. Always does a bcrypt comparison
    (against a dummy hash if `stored` is missing/legacy) to keep timing even."""
    target = stored if is_hashed(stored) else DUMMY_HASH
    try:
        ok = bcrypt.checkpw(_prep(password), target.encode("utf-8"))
    except ValueError:
        return False
    return ok and is_hashed(stored)


def password_problem(password, username=""):
    """Return a message if the password is too weak, else None."""
    if len(password) < 8:
        return "Password must be at least 8 characters."
    if not any(c.isalpha() for c in password) or not any(c.isdigit() for c in password):
        return "Password must contain at least one letter and one number."
    if username and password.lower() == username.lower():
        return "Password must not be the same as the username."
    if password == DEFAULT_SEED_PASSWORD:
        return "Please choose a password other than the default one."
    return None
