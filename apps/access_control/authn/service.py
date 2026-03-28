"""Authentication and PAT lifecycle service."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.access_control.authn.models import PATCreateResponse, PATResponse, TokenPrincipalResponse
from libs.db_models import PersonalAccessToken, User

_PAT_PREFIX = "pat_"


class AuthenticationError(ValueError):
    """Raised when token validation fails."""


@dataclass(frozen=True)
class JWTSettings:
    """JWT validation settings."""

    secret: str
    issuer: str | None = None
    audience: str | None = None


class AuthnService:
    """JWT and PAT authentication helpers backed by PostgreSQL."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        jwt_settings: JWTSettings,
    ) -> None:
        self._session = session
        self._jwt_settings = jwt_settings

    async def validate_token(self, token: str) -> TokenPrincipalResponse:
        if token.startswith(_PAT_PREFIX):
            return await self._validate_pat(token)
        return self._validate_jwt(token)

    async def create_pat(
        self,
        *,
        username: str,
        name: str,
        email: str | None = None,
    ) -> PATCreateResponse:
        user = await self._get_or_create_user(username=username, email=email)
        plaintext_token = _generate_pat()
        record = PersonalAccessToken(
            user_id=user.id,
            token_hash=_hash_token(plaintext_token),
            name=name,
        )
        self._session.add(record)
        await self._session.commit()
        await self._session.refresh(record)
        return PATCreateResponse(
            id=record.id,
            username=user.username,
            name=record.name,
            token=plaintext_token,
            created_at=record.created_at,
            revoked_at=record.revoked_at,
        )

    async def list_pats(self, *, username: str) -> list[PATResponse]:
        result = await self._session.execute(
            select(PersonalAccessToken, User)
            .join(User, PersonalAccessToken.user_id == User.id)
            .where(User.username == username)
            .order_by(PersonalAccessToken.created_at.desc())
            .limit(1000)
        )
        return [
            PATResponse(
                id=pat.id,
                username=user.username,
                name=pat.name,
                created_at=pat.created_at,
                revoked_at=pat.revoked_at,
            )
            for pat, user in result.all()
        ]

    async def revoke_pat(self, pat_id: UUID) -> PATResponse | None:
        result = await self._session.execute(
            select(PersonalAccessToken, User)
            .join(User, PersonalAccessToken.user_id == User.id)
            .where(PersonalAccessToken.id == pat_id)
        )
        row = result.first()
        if row is None:
            return None

        pat, user = row
        if pat.revoked_at is None:
            pat.revoked_at = datetime.now(UTC)
            await self._session.commit()
            await self._session.refresh(pat)

        return PATResponse(
            id=pat.id,
            username=user.username,
            name=pat.name,
            created_at=pat.created_at,
            revoked_at=pat.revoked_at,
        )

    async def _validate_pat(self, token: str) -> TokenPrincipalResponse:
        token_hash = _hash_token(token)
        result = await self._session.execute(
            select(PersonalAccessToken, User)
            .join(User, PersonalAccessToken.user_id == User.id)
            .where(PersonalAccessToken.token_hash == token_hash)
        )
        row = result.first()
        if row is None:
            raise AuthenticationError("PAT is invalid.")

        pat, user = row
        if pat.revoked_at is not None:
            raise AuthenticationError("PAT has been revoked.")

        return TokenPrincipalResponse(
            subject=user.username,
            token_type="pat",
            claims={
                "sub": user.username,
                "pat_id": str(pat.id),
                "name": pat.name,
            },
        )

    def _validate_jwt(self, token: str) -> TokenPrincipalResponse:
        parts = token.split(".")
        if len(parts) != 3:
            raise AuthenticationError("JWT must contain three segments.")

        header_segment, payload_segment, signature_segment = parts
        try:
            header = json.loads(_b64decode_json(header_segment))
        except (json.JSONDecodeError, UnicodeDecodeError, Exception):
            raise AuthenticationError("Malformed JWT header.")
        if header.get("alg") != "HS256":
            raise AuthenticationError("Unsupported JWT algorithm.")

        signing_input = f"{header_segment}.{payload_segment}".encode()
        expected_signature = hmac.new(
            self._jwt_settings.secret.encode("utf-8"),
            signing_input,
            hashlib.sha256,
        ).digest()
        try:
            provided_signature = _b64decode_bytes(signature_segment)
        except Exception:
            raise AuthenticationError("Malformed JWT signature encoding.")
        if not hmac.compare_digest(expected_signature, provided_signature):
            raise AuthenticationError("JWT signature is invalid.")

        try:
            claims = json.loads(_b64decode_json(payload_segment))
        except (json.JSONDecodeError, UnicodeDecodeError, Exception):
            raise AuthenticationError("Malformed JWT payload.")
        now_ts = int(datetime.now(UTC).timestamp())
        exp = claims.get("exp")
        if not isinstance(exp, int) or exp <= now_ts:
            raise AuthenticationError("JWT is expired.")

        nbf = claims.get("nbf")
        if isinstance(nbf, int) and nbf > now_ts:
            raise AuthenticationError("JWT is not active yet.")

        if self._jwt_settings.issuer and claims.get("iss") != self._jwt_settings.issuer:
            raise AuthenticationError("JWT issuer is invalid.")

        if self._jwt_settings.audience and not _audience_matches(
            claims.get("aud"),
            self._jwt_settings.audience,
        ):
            raise AuthenticationError("JWT audience is invalid.")

        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject:
            raise AuthenticationError("JWT subject is missing.")

        return TokenPrincipalResponse(
            subject=subject,
            token_type="jwt",
            claims=claims,
        )

    async def _get_or_create_user(self, *, username: str, email: str | None) -> User:
        result = await self._session.execute(select(User).where(User.username == username))
        user = result.scalar_one_or_none()
        if user is not None:
            if email and user.email != email:
                user.email = email
                await self._session.commit()
                await self._session.refresh(user)
            return user

        user = User(username=username, email=email)
        self._session.add(user)
        await self._session.commit()
        await self._session.refresh(user)
        return user


def load_jwt_settings() -> JWTSettings:
    """Load JWT settings from environment variables."""

    secret = os.getenv("ACCESS_CONTROL_JWT_SECRET")
    if not secret:
        env = os.getenv("ENV", "dev")
        if env.lower() not in ("dev", "development", "test"):
            raise RuntimeError("ACCESS_CONTROL_JWT_SECRET must be set in non-dev environments")
        secret = "dev-secret"
    issuer = os.getenv("ACCESS_CONTROL_JWT_ISSUER")
    audience = os.getenv("ACCESS_CONTROL_JWT_AUDIENCE")
    return JWTSettings(secret=secret, issuer=issuer, audience=audience)


def _generate_pat() -> str:
    return f"{_PAT_PREFIX}{secrets.token_urlsafe(32)}"


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def hash_token_value(token: str) -> str:
    """Expose PAT hashing for gateway binding and reconciliation."""

    return _hash_token(token)


def _b64decode_json(segment: str) -> str:
    return _b64decode_bytes(segment).decode("utf-8")


def _b64decode_bytes(segment: str) -> bytes:
    padding = "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(segment + padding)


def _audience_matches(value: Any, expected_audience: str) -> bool:
    if isinstance(value, str):
        return value == expected_audience
    if isinstance(value, list):
        return expected_audience in value
    return False
