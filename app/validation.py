import re

from app.config import get_settings

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def check_email(v: str) -> str:
    v = v.strip().lower()
    if len(v) > 254 or not _EMAIL_RE.match(v):
        raise ValueError("Enter a valid email address.")
    return v


def check_password(v: str) -> str:
    n = get_settings().password_min_length
    if len(v) < n:
        raise ValueError(f"Password must be at least {n} characters.")
    if len(v) > 200:
        raise ValueError("Password is too long.")
    return v


def check_name(v: str) -> str:
    v = " ".join(v.split())
    if not v:
        raise ValueError("Enter a name.")
    if len(v) > 100:
        raise ValueError("Name is too long.")
    return v
