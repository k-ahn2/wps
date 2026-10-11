import base64
import hashlib
import hmac
import os
import time

# dashboard_auth.py: password hashing and session tokens for the web dashboard (dashboard.py).
# Standard library only, and imports nothing from WPS, so env.py can use it to migrate an old
# plaintext dashboard password.
#
# The password is stored in env.json as dashboard.passwordHash, never in plaintext:
# "pbkdf2_sha256$<iterations>$<salt b64>$<hash b64>". A session is a cookie holding
# "<expiry epoch s>.<HMAC of the expiry>", keyed by that stored hash - so changing (or clearing)
# the password ends every existing session, and no session secret has to be stored anywhere.

ITERATIONS = 150000
SESSION_SECONDS = 30 * 86400
MIN_PASSWORD_LENGTH = 8


def hash_password(password):
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS)
    return f"pbkdf2_sha256${ITERATIONS}${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def verify_password(password, stored):
    try:
        scheme, iterations, salt, digest = stored.split("$")
        if scheme != "pbkdf2_sha256":
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), base64.b64decode(salt), int(iterations))
        return hmac.compare_digest(actual, base64.b64decode(digest))
    except (ValueError, AttributeError):
        return False


def _sign(stored, expiry):
    return hmac.new(stored.encode(), str(expiry).encode(), hashlib.sha256).hexdigest()


def make_session(stored):
    expiry = int(time.time()) + SESSION_SECONDS
    return f"{expiry}.{_sign(stored, expiry)}"


def check_session(stored, token):
    if not stored or not token:
        return False
    try:
        expiry, signature = token.split(".", 1)
        return int(expiry) > time.time() and hmac.compare_digest(signature, _sign(stored, int(expiry)))
    except ValueError:
        return False
