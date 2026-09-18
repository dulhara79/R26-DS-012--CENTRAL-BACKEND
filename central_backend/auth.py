"""End-user JWT verification for Handbook Phase 1.

Production verification is deliberately configuration-driven. The inspected
project sources do not define a production signing algorithm, issuer, audience,
JWKS endpoint, public key or shared JWT secret, so this module refuses to guess.

Required runtime configuration:
  AUTH_JWT_ISSUER
  AUTH_JWT_AUDIENCE
  AUTH_JWT_ALGORITHMS       comma-separated allow-list
and exactly one key source:
  AUTH_JWT_JWKS_URL
  AUTH_JWT_PUBLIC_KEY
  AUTH_JWT_SECRET

The last option is primarily useful for controlled research deployments/tests;
asymmetric keys/JWKS are preferable when supported by the approved auth service.
"""

from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass
from typing import Any, Optional
from uuid import UUID

import jwt
from fastapi import Header, HTTPException
from jwt import PyJWKClient
from jwt.exceptions import (
    ExpiredSignatureError,
    InvalidAudienceError,
    InvalidIssuerError,
    InvalidTokenError,
    MissingRequiredClaimError,
)

from schemas.phase0_v1 import PrincipalType


class AuthConfigurationError(RuntimeError):
    pass


@dataclass(frozen=True)
class VerifiedPrincipal:
    principal_type: PrincipalType
    principal_id: str
    role: str
    clinician_id: Optional[str]
    subject_id: Optional[str]
    expires_at: dt.datetime
    issuer: str
    audience: Any

    @property
    def actor_id(self) -> str:
        if self.principal_type == PrincipalType.CLINICIAN and self.clinician_id:
            return self.clinician_id
        if self.principal_type == PrincipalType.PATIENT and self.subject_id:
            return self.subject_id
        return self.principal_id


def _csv(name: str) -> list[str]:
    return [x.strip() for x in os.getenv(name, "").split(",") if x.strip()]


def auth_configured() -> bool:
    key_sources = [
        bool(os.getenv("AUTH_JWT_JWKS_URL", "").strip()),
        bool(os.getenv("AUTH_JWT_PUBLIC_KEY", "").strip()),
        bool(os.getenv("AUTH_JWT_SECRET", "").strip()),
    ]
    return bool(
        os.getenv("AUTH_JWT_ISSUER", "").strip()
        and os.getenv("AUTH_JWT_AUDIENCE", "").strip()
        and _csv("AUTH_JWT_ALGORITHMS")
        and sum(key_sources) == 1
    )


def _configuration() -> tuple[str, str, list[str], str, str]:
    issuer = os.getenv("AUTH_JWT_ISSUER", "").strip()
    audience = os.getenv("AUTH_JWT_AUDIENCE", "").strip()
    algorithms = _csv("AUTH_JWT_ALGORITHMS")
    jwks_url = os.getenv("AUTH_JWT_JWKS_URL", "").strip()
    public_key = os.getenv("AUTH_JWT_PUBLIC_KEY", "").replace("\\n", "\n").strip()
    secret = os.getenv("AUTH_JWT_SECRET", "").strip()

    if not issuer or not audience or not algorithms:
        raise AuthConfigurationError(
            "JWT verification is not configured: set AUTH_JWT_ISSUER, "
            "AUTH_JWT_AUDIENCE and AUTH_JWT_ALGORITHMS."
        )
    if any(a.lower() == "none" for a in algorithms):
        raise AuthConfigurationError("AUTH_JWT_ALGORITHMS must never allow 'none'.")

    key_sources = [(name, value) for name, value in (
        ("jwks", jwks_url), ("public_key", public_key), ("secret", secret)
    ) if value]
    if len(key_sources) != 1:
        raise AuthConfigurationError(
            "Configure exactly one JWT key source: AUTH_JWT_JWKS_URL, "
            "AUTH_JWT_PUBLIC_KEY or AUTH_JWT_SECRET."
        )
    key_kind, key_value = key_sources[0]
    return issuer, audience, algorithms, key_kind, key_value


def _bearer_token(authorization: Optional[str]) -> str:
    if not authorization:
        raise HTTPException(status_code=401, detail="missing bearer token")
    scheme, sep, token = authorization.partition(" ")
    if not sep or scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(status_code=401, detail="invalid bearer token")
    return token.strip()


def _decode(token: str) -> dict:
    try:
        issuer, audience, algorithms, key_kind, key_value = _configuration()
    except AuthConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    try:
        if key_kind == "jwks":
            signing_key = PyJWKClient(key_value).get_signing_key_from_jwt(token).key
        else:
            signing_key = key_value

        claims = jwt.decode(
            token,
            signing_key,
            algorithms=algorithms,
            issuer=issuer,
            audience=audience,
            options={"require": ["exp", "iss", "aud", "sub", "role"]},
        )
    except ExpiredSignatureError as exc:
        raise HTTPException(status_code=401, detail="session expired") from exc
    except (InvalidAudienceError, InvalidIssuerError, MissingRequiredClaimError,
            InvalidTokenError) as exc:
        raise HTTPException(status_code=401, detail="invalid bearer token") from exc
    except Exception as exc:
        # Key retrieval/verification must fail closed and never fall back to an
        # unverified token or a shared mobile credential.
        raise HTTPException(status_code=401, detail="invalid bearer token") from exc

    return claims


def verify_authorization(authorization: Optional[str]) -> VerifiedPrincipal:
    claims = _decode(_bearer_token(authorization))

    role = str(claims.get("role", "")).strip()
    sub = str(claims.get("sub", "")).strip()
    if not role or not sub:
        raise HTTPException(status_code=401, detail="invalid bearer token")

    principal_raw = str(claims.get("principal_type") or role).strip().lower()
    try:
        principal_type = PrincipalType(principal_raw)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="unsupported principal type") from exc

    clinician_id: Optional[str] = None
    subject_id: Optional[str] = None

    if principal_type == PrincipalType.CLINICIAN:
        clinician_id = str(claims.get("clinician_id") or sub).strip()
        if not clinician_id:
            raise HTTPException(status_code=401, detail="clinician identity missing from token")
    elif principal_type == PrincipalType.PATIENT:
        subject_id = str(claims.get("subject_id") or "").strip()
        try:
            UUID(subject_id)
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=401, detail="patient subject binding missing from token") from exc

    exp = claims.get("exp")
    expires_at = dt.datetime.fromtimestamp(float(exp), tz=dt.timezone.utc)

    return VerifiedPrincipal(
        principal_type=principal_type,
        principal_id=sub,
        role=role,
        clinician_id=clinician_id,
        subject_id=subject_id,
        expires_at=expires_at,
        issuer=str(claims.get("iss", "")),
        audience=claims.get("aud"),
    )


def current_principal(authorization: Optional[str] = Header(None)) -> VerifiedPrincipal:
    return verify_authorization(authorization)
