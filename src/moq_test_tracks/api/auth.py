# Copyright (c) OpenMOQ contributors.
# This source code is licensed under the Apache 2.0 license found in the
# LICENSE file in the root directory of this source tree.

# Self-contained tokens (docs/admin.md#tokens), roles, dashboard sessions,
# revocation, and the failed-login limiter.

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
import secrets
import time
from collections import defaultdict, deque
from dataclasses import dataclass

import cbor2

from .db import Db

ROLES = ("view", "run", "admin")  # each role includes the ones before it
TIERS = ("standard", "unrestricted")
PREFIX = "mtt1_"
ISSUER = "mtt"
CLAIM = "mtt"  # private claim {1: role, 2: relays, 3: tier (absent: standard)}
ROLE, RELAYS, TIER = 1, 2, 3
DEFAULT_DAYS = 30
MAX_DAYS = 365
UNRESTRICTED_MAX_DAYS = 30
SESSION_TTL_S = 12 * 3600
MAX_TOKEN_CHARS = 4096
CLOCK_SKEW_S = 300
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._@-]{0,63}")

# CWT claim keys (RFC 8392) and COSE values (RFC 9052, RFC 9053).
ISS, SUB, EXP, IAT, CTI = 1, 2, 4, 6, 7
COSE_ALG, COSE_KID, HMAC_256_256 = 1, 4, 5
TAG_CWT, TAG_MAC0 = 61, 17
HEAD = b"\xd8\x3d\xd1\x84"  # tag 61, tag 17, array of 4
EMPTY_MAP = b"\xa0"


@dataclass(frozen=True)
class Principal:
    token_id: str
    name: str
    role: str
    tier: str = "standard"
    relays: tuple[str, ...] = ()
    expires: float = 0.0

    def can(self, role: str) -> bool:
        return ROLES.index(self.role) >= ROLES.index(role)

    @property
    def exempt(self) -> bool:
        """Exempt from the per-token limits (the global caps still apply)."""
        return self.tier == "unrestricted" or self.can("admin")

    def info(self) -> dict:
        return {
            "token_id": self.token_id,
            "name": self.name,
            "role": self.role,
            "tier": self.tier,
            "relays": list(self.relays),
            "expires": self.expires,
        }


def _bstr(data: bytes, i: int) -> tuple[bytes, int]:
    """The definite-length CBOR byte string at i, and the offset after it."""
    if data[i] >> 5 != 2:
        raise ValueError("not a byte string")
    info, i = data[i] & 0x1F, i + 1
    if info < 24:
        n = info
    elif info == 24:
        n, i = data[i], i + 1
    elif info == 25:
        n, i = int.from_bytes(data[i : i + 2], "big"), i + 2
    else:
        raise ValueError("byte string too long")
    if i + n > len(data):
        raise ValueError("truncated")
    return data[i : i + n], i + n


class Signer:
    """COSE_Mac0 (HMAC 256/256) over a CWT claims set, tagged CWT."""

    def __init__(self, secret: str):
        if len(secret) < 32:
            raise ValueError("a signing secret must be at least 32 characters")
        self._key = secret.encode()
        kid = hashlib.sha256(b"mtt kid" + self._key).digest()[:4]
        self._protected = cbor2.dumps({COSE_ALG: HMAC_256_256, COSE_KID: kid})

    def _mac(self, payload: bytes) -> bytes:
        structure = cbor2.dumps(["MAC0", self._protected, b"", payload])
        return hmac.new(self._key, structure, hashlib.sha256).digest()

    def sign(self, claims: dict) -> str:
        payload = cbor2.dumps(claims)
        mac0 = cbor2.CBORTag(TAG_MAC0, [self._protected, {}, payload, self._mac(payload)])
        raw = cbor2.dumps(cbor2.CBORTag(TAG_CWT, mac0))
        return PREFIX + base64.urlsafe_b64encode(raw).decode().rstrip("=")

    def open(self, token: str) -> dict | None:
        """The claims of a token this signer made, else None. Only the MAC'd
        payload is CBOR-decoded."""
        if not token.startswith(PREFIX) or len(token) > MAX_TOKEN_CHARS:
            return None
        body = token[len(PREFIX) :]
        try:
            raw = base64.b64decode(body + "=" * (-len(body) % 4), altchars=b"-_", validate=True)
            if not raw.startswith(HEAD):
                return None
            protected, i = _bstr(raw, len(HEAD))
            if raw[i : i + 1] != EMPTY_MAP:
                return None
            payload, i = _bstr(raw, i + 1)
            tag, i = _bstr(raw, i)
        except (ValueError, IndexError, binascii.Error):
            return None
        if i != len(raw) or not hmac.compare_digest(protected, self._protected):
            return None
        if not hmac.compare_digest(tag, self._mac(payload)):
            return None
        claims = cbor2.loads(payload)
        return claims if isinstance(claims, dict) else None


def _claims(p: Principal, issued: float) -> dict:
    grant = {ROLE: p.role, RELAYS: list(p.relays)}
    if p.tier != "standard":
        grant[TIER] = p.tier
    return {
        ISS: ISSUER,
        SUB: p.name,
        EXP: int(p.expires),
        IAT: int(issued),
        CTI: bytes.fromhex(p.token_id),
        CLAIM: grant,
    }


def principal(claims: dict) -> Principal | None:
    """The principal a well-formed claims set describes; times are not checked."""
    m = claims.get(CLAIM)
    cti, exp, iat = claims.get(CTI), claims.get(EXP), claims.get(IAT)
    if claims.get(ISS) != ISSUER or not isinstance(m, dict):
        return None
    if not (isinstance(exp, int) and isinstance(iat, int) and isinstance(cti, bytes)):
        return None
    name, role, relays = claims.get(SUB), m.get(ROLE), m.get(RELAYS)
    tier = m.get(TIER, "standard")
    if not isinstance(name, str) or role not in ROLES or tier not in TIERS:
        return None
    if not (isinstance(relays, list) and all(isinstance(r, str) for r in relays)):
        return None
    return Principal(cti.hex(), name, role, tier, tuple(relays), float(exp))


def mint(
    signer: Signer,
    name: str,
    role: str,
    *,
    relays: tuple[str, ...] | list[str] = (),
    tier: str = "standard",
    expires_days: float = DEFAULT_DAYS,
    now: float | None = None,
) -> tuple[str, Principal]:
    """A new token and its principal; the token string is the only copy."""
    if not NAME.fullmatch(name):
        raise ValueError("name: 1-64 letters, digits, space, '.', '_', '@' or '-'")
    if role not in ROLES:
        raise ValueError(f"role must be one of {', '.join(ROLES)}")
    if tier not in TIERS:
        raise ValueError(f"tier must be one of {', '.join(TIERS)}")
    if not 0 < expires_days <= MAX_DAYS:
        raise ValueError(f"tokens must expire within {MAX_DAYS} days")
    if tier == "unrestricted" and expires_days > UNRESTRICTED_MAX_DAYS:
        raise ValueError(f"unrestricted tokens must expire within {UNRESTRICTED_MAX_DAYS} days")
    relays = tuple(dict.fromkeys(relays))
    if any(not r for r in relays):
        raise ValueError("relay ids must not be empty")
    now = time.time() if now is None else now
    expires = float(int(now + expires_days * 86400))
    p = Principal(secrets.token_hex(8), name, role, tier, relays, expires)
    return signer.sign(_claims(p, now)), p


def is_revoked(db: Db, token_id: str) -> bool:
    return bool(db.query("SELECT 1 FROM revoked WHERE id = ?", (token_id,)))


def verify(signer: Signer, db: Db, token: str) -> Principal | None:
    """The principal of a current, unrevoked token this signer made."""
    claims = signer.open(token)
    p = principal(claims) if claims is not None else None
    now = time.time()
    if p is None or p.expires <= now or claims[IAT] > now + CLOCK_SKEW_S:
        return None
    return None if is_revoked(db, p.token_id) else p


def revoke(db: Db, token_id: str, reason: str = "") -> bool:
    """Revoke a token by id, issued here or not; False if it already was."""
    if is_revoked(db, token_id):
        return False
    db.execute(
        "INSERT INTO revoked (id, time, reason) VALUES (?, ?, ?)", (token_id, time.time(), reason)
    )
    return True


def record(db: Db, p: Principal) -> None:
    """Log an issued token's claims (never the token) for `mtt tokens list`."""
    db.audit("token_created", p.token_id, None, json.dumps(p.info()))


def issued(db: Db) -> list[dict]:
    """Issued tokens from the audit log, each with its status."""
    revoked = {r["id"] for r in db.query("SELECT id FROM revoked")}
    now = time.time()
    out = []
    rows = db.query("SELECT time, detail FROM audit WHERE action = 'token_created' ORDER BY time")
    for row in rows:
        info = {**json.loads(row["detail"]), "issued": row["time"]}
        if info["token_id"] in revoked:
            info["status"] = "revoked"
        elif info["expires"] <= now:
            info["status"] = "expired"
        else:
            info["status"] = "active"
        out.append(info)
    return out


class Sessions:
    """Dashboard sessions: the presented token's claims for at most 12 h,
    signed with the session secret. Revoking the token ends them."""

    def __init__(self, secret: str, db: Db):
        self._signer = Signer(secret)
        self._db = db

    def issue(self, p: Principal) -> tuple[str, int]:
        """The cookie value and its lifetime in seconds."""
        now = time.time()
        expires = min(p.expires, float(int(now + SESSION_TTL_S)))
        session = Principal(p.token_id, p.name, p.role, p.tier, p.relays, expires)
        return self._signer.sign(_claims(session, now)), max(1, int(expires - now))

    def verify(self, value: str) -> Principal | None:
        return verify(self._signer, self._db, value)


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
