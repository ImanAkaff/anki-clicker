"""
Who is allowed to do what.

Each friend gets a long random token (like a password generated for them).
The add-on sends it with every request in an HTTP header:

    Authorization: Bearer <token>

"Bearer" just means "whoever bears (holds) this token is allowed in".

Storing tokens: we keep only a SHA-256 hash. Hashing is one-way: easy to turn
a token into its hash, practically impossible to go back. To check a token,
we hash what was sent and look up that hash.

(For human-chosen passwords you'd use a deliberately slow hash like bcrypt,
because people pick guessable passwords. These tokens are 256 bits of
randomness, so nobody can guess them and a fast hash is fine.)
"""

import hashlib
import secrets

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import config
from .db import get_db
from .models import User

# Reads the "Authorization: Bearer ..." header for us.
# auto_error=False so we can return our own clear error message.
_bearer = HTTPBearer(auto_error=False)


def new_token() -> str:
    # `secrets` uses the operating system's cryptographic randomness.
    # Never use `random` for anything security-related; it's predictable.
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> User:
    """Dependency: endpoints that declare `user = Depends(current_user)`
    only run if a valid token was sent, and receive that friend's User row."""
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing token")
    user = db.scalar(select(User).where(User.token_hash == hash_token(creds.credentials)))
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token")
    return user


def require_admin(creds: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> None:
    """Dependency for endpoints only you should call."""
    if not config.ADMIN_TOKEN:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin endpoints are disabled")
    # compare_digest takes the same time whether the first character or the
    # last one is wrong, so an attacker can't time responses to guess it.
    if creds is None or not secrets.compare_digest(creds.credentials, config.ADMIN_TOKEN):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not an admin")
