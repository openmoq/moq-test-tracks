# Copyright (c) OpenMOQ contributors.
# This source code is licensed under the Apache 2.0 license found in the
# LICENSE file in the root directory of this source tree.

# API keys (mtt_<id>_<secret>, only the secret's SHA-256 stored), roles,
# signed session cookies, and the failed-login limiter.

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from collections import defaultdict, deque
from dataclasses import dataclass

from .db import Db

ROLES = ("view", "run", "admin")  # each role includes the ones before it
PREFIX = "mtt"
SESSION_TTL_S = 12 * 3600
UNRESTRICTED_MAX_DAYS = 30


@dataclass(frozen=True)
class Principal:
    key_id: str
    name: str
    role: str
    unrestricted: bool = False

    def can(self, role: str) -> bool:
        return ROLES.index(self.role) >= ROLES.index(role)

    @property
    def exempt(self) -> bool:
        """Exempt from the per-key limits (the global caps still apply)."""
        return self.unrestricted or self.can("admin")


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def new_key(
    db: Db, name: str, role: str, expires_days: float | None = None, unrestricted: bool = False
) -> str:
    """Create a key; the returned string is the only copy of its secret."""
    if role not in ROLES:
        raise ValueError(f"role must be one of {', '.join(ROLES)}")
    if unrestricted and not (expires_days and 0 < expires_days <= UNRESTRICTED_MAX_DAYS):
        raise ValueError(f"unrestricted keys must expire within {UNRESTRICTED_MAX_DAYS} days")
    key_id = base64.b32encode(secrets.token_bytes(5)).decode().lower()
    secret = secrets.token_urlsafe(32)
    now = time.time()
    expires = now + expires_days * 86400 if expires_days else None
    db.execute(
        "INSERT INTO keys (id, name, role, secret_hash, created, expires, unrestricted)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (key_id, name, role, _hash(secret), now, expires, int(unrestricted)),
    )
    return f"{PREFIX}_{key_id}_{secret}"


def parse(key: str) -> tuple[str, str] | None:
    parts = key.split("_", 2)
    if len(parts) != 3 or parts[0] != PREFIX or not parts[1] or not parts[2]:
        return None
    return parts[1], parts[2]


def _principal(db: Db, key_id: str) -> Principal | None:
    rows = db.query(
        "SELECT id, name, role, expires, revoked, unrestricted FROM keys WHERE id = ?", (key_id,)
    )
    if not rows:
        return None
    row = rows[0]
    if row["revoked"] is not None or (row["expires"] is not None and row["expires"] < time.time()):
        return None
    return Principal(row["id"], row["name"], row["role"], bool(row["unrestricted"]))


def verify(db: Db, key: str) -> Principal | None:
    parsed = parse(key)
    if parsed is None:
        return None
    key_id, secret = parsed
    rows = db.query("SELECT secret_hash FROM keys WHERE id = ?", (key_id,))
    if not rows or not hmac.compare_digest(rows[0]["secret_hash"], _hash(secret)):
        return None
    principal = _principal(db, key_id)
    if principal is not None:
        db.execute("UPDATE keys SET last_used = ? WHERE id = ?", (time.time(), key_id))
    return principal


def list_keys(db: Db) -> list[dict]:
    rows = db.query(
        "SELECT id, name, role, created, expires, revoked, last_used, unrestricted FROM keys"
        " ORDER BY created"
    )
    return [dict(r) for r in rows]


def revoke(db: Db, key_id: str) -> bool:
    return (
        db.execute(
            "UPDATE keys SET revoked = ? WHERE id = ? AND revoked IS NULL", (time.time(), key_id)
        )
        == 1
    )


class Sessions:
    """Cookie value <key_id>.<expiry>.<hmac>; the key is re-checked on every use,
    so revoking it ends its sessions."""

    def __init__(self, secret: str, db: Db):
        if len(secret) < 32:
            raise ValueError("the session secret must be at least 32 characters")
        self._secret = secret.encode()
        self._db = db

    def _sign(self, payload: str) -> str:
        return hmac.new(self._secret, payload.encode(), hashlib.sha256).hexdigest()

    def issue(self, principal: Principal) -> str:
        payload = f"{principal.key_id}.{int(time.time()) + SESSION_TTL_S}"
        return f"{payload}.{self._sign(payload)}"

    def verify(self, value: str) -> Principal | None:
        key_id, _, rest = value.partition(".")
        expiry, _, sig = rest.partition(".")
        if not (key_id and expiry.isdigit() and sig):
            return None
        if not hmac.compare_digest(sig, self._sign(f"{key_id}.{expiry}")):
            return None
        if int(expiry) < time.time():
            return None
        return _principal(self._db, key_id)


class LoginLimiter:
    """At most `limit` failed authentications per address per `window_s`."""

    def __init__(self, limit: int = 10, window_s: float = 3600):
        self.limit = limit
        self.window_s = window_s
        self._fails: dict[str, deque] = defaultdict(deque)

    def _trim(self, ip: str) -> deque:
        q = self._fails[ip]
        while q and q[0] < time.monotonic() - self.window_s:
            q.popleft()
        return q

    def blocked(self, ip: str) -> bool:
        return len(self._trim(ip)) >= self.limit

    def fail(self, ip: str) -> None:
        self._trim(ip).append(time.monotonic())
