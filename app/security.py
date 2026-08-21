from __future__ import annotations

import hashlib
import hmac
import os
import random
import string
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from . import config

bearer = HTTPBearer(auto_error=False)


# ---------------------------------------------------------------- passwords
def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 120_000)
    return f"pbkdf2${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt_hex, dk_hex = stored.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), 120_000)
        return hmac.compare_digest(dk.hex(), dk_hex)
    except Exception:
        return False


# ---------------------------------------------------------------- identity
def hash_mrn(mrn: str) -> str:
    """MRN never touches the database in the clear."""
    return hashlib.sha256((mrn.strip().upper() + config.MRN_PEPPER).encode()).hexdigest()


def new_subject_id(mrn_hash: str) -> str:
    return "S-" + mrn_hash[:10]


def new_pairing_code() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no I/O/0/1
    part = lambda: "".join(random.choice(alphabet) for _ in range(4))
    return f"{part()}-{part()}"


# ---------------------------------------------------------------- tokens
def make_token(sub: str, role: str, subject_id: str | None = None) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": sub,
        "role": role,                 # clinician | patient
        "subject_id": subject_id,     # patients are scoped to exactly one subject
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=config.JWT_TTL_MIN)).timestamp()),
    }
    return jwt.encode(payload, config.JWT_SECRET, algorithm=config.JWT_ALGO)


def _decode(token: str) -> dict:
    try:
        return jwt.decode(token, config.JWT_SECRET, algorithms=[config.JWT_ALGO])
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or expired token")


def current_principal(creds: HTTPAuthorizationCredentials | None = Depends(bearer)) -> dict:
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
    return _decode(creds.credentials)


def require_clinician(p: dict = Depends(current_principal)) -> dict:
    if p.get("role") != "clinician":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "clinician role required")
    return p


def require_patient(p: dict = Depends(current_principal)) -> dict:
    if p.get("role") != "patient" or not p.get("subject_id"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "patient role required")
    return p
