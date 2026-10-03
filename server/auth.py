"""Multi-user auth: scrypt password hashing, opaque sessions, roles.

Roles: admin (mutates) / viewer (read-only). Session token: urlsafe random,
sha256 hash stored in DB, HttpOnly SameSite=Strict cookie, sliding 30-day TTL.
CSRF: SameSite=Strict + required X-Requested-With header on non-GET /api
(enforced in api.py middleware).
"""
from __future__ import annotations

import hashlib
import secrets as pysecrets
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request

from . import db

SCRYPT_N, SCRYPT_R = 2**14, 8


def hash_password(password: str) -> str:
    salt = pysecrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=1, dklen=32)
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, n, r, salt_hex, dk_hex = stored.split("$")
        dk = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex),
                            n=int(n), r=int(r), p=1, dklen=32)
        return pysecrets.compare_digest(dk.hex(), dk_hex)
    except (ValueError, TypeError):
        return False


COOKIE = "omni_session"


@dataclass
class User:
    id: int
    username: str
    role: str
    disabled: bool


def create_user(username: str, password: str, role: str = "viewer") -> int:
    with db.connect() as conn:
        cur = conn.execute(
            "INSERT INTO users(username, password_hash, role, created_at) VALUES(?,?,?,?)",
            (username, hash_password(password), role, db.now()),
        )
        return cur.lastrowid


def login(username: str, password: str) -> tuple[str, User] | None:
    """Returns (session_token, user) or None. Caller sets the cookie."""
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    if not row or row["disabled"] or not verify_password(password, row["password_hash"]):
        return None
    token = pysecrets.token_urlsafe(32)
    from datetime import datetime, timedelta, timezone
    from . import config
    expires = (datetime.now(timezone.utc)
               + timedelta(days=config.SESSION_TTL_DAYS)).isoformat(timespec="seconds")
    with db.connect() as conn:
        conn.execute("INSERT INTO sessions(token_hash, user_id, created_at, expires_at) VALUES(?,?,?,?)",
                     (_hash(token), row["id"], db.now(), expires))
    return token, User(row["id"], row["username"], row["role"], bool(row["disabled"]))


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def logout(token: str) -> None:
    with db.connect() as conn:
        conn.execute("DELETE FROM sessions WHERE token_hash=?", (_hash(token),))


def user_for_token(token: str) -> User | None:
    with db.connect() as conn:
        row = conn.execute(
            """SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id
               WHERE s.token_hash=? AND s.expires_at > ?""",
            (_hash(token), db.now()),
        ).fetchone()
    if not row or row["disabled"]:
        return None
    return User(row["id"], row["username"], row["role"], bool(row["disabled"]))


def user_count() -> int:
    with db.connect() as conn:
        return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]


def require_user(request: Request) -> User:
    token = request.cookies.get(COOKIE)
    user = user_for_token(token) if token else None
    if user is None:
        raise HTTPException(status_code=401, detail="Not signed in")
    return user


def require_admin(user: User = Depends(require_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")
    return user
