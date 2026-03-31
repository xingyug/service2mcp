"""Authentication and PAT lifecycle service."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.access_control.authn.models import (
    PATCreateResponse,
    PATListResponse,
    PATResponse,
    TokenPrincipalResponse,
)
from libs.db_models import PersonalAccessToken, User

_PAT_PREFIX = "pat_"
_USERNAME_CLAIM_KEYS = (
    "preferred_username",
    "username",
    "cognito:username",
    "login",
)


class AuthenticationError(ValueError):
    """Raised when token validation fails."""


class UserNotFoundError(LookupError):
    """Raised when PAT management targets an unknown local user."""


class JWTConfigurationError(RuntimeError):
    """Raised when JWT settings are missing or invalid."""


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
        commit: bool = True,
    ) -> PATCreateResponse:
        user = await self._get_existing_user(username=username)
        plaintext_token = _generate_pat()
        record = PersonalAccessToken(
            user_id=user.id,
            token_hash=_hash_token(plaintext_token),
            name=name,
        )
        self._session.add(record)
        await self._session.flush()
        if commit:
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

    async def list_pats(
        self,
        *,
        username: str,
        page: int = 1,
        page_size: int = 100,
    ) -> PATListResponse:
        page = max(page, 1)
        page_size = max(page_size, 1)

        total_result = await self._session.execute(
            select(func.count(PersonalAccessToken.id))
            .join(User, PersonalAccessToken.user_id == User.id)
            .where(User.username == username)
        )
        total = int(total_result.scalar_one() or 0)
        total_pages = max(1, (total + page_size - 1) // page_size)
        effective_page = min(page, total_pages)
        offset = (effective_page - 1) * page_size

        result = await self._session.execute(
            select(PersonalAccessToken, User)
            .join(User, PersonalAccessToken.user_id == User.id)
            .where(User.username == username)
            .order_by(PersonalAccessToken.created_at.desc(), PersonalAccessToken.id.desc())
            .limit(page_size)
            .offset(offset)
        )
        return PATListResponse(
            items=[
                PATResponse(
                    id=pat.id,
                    username=user.username,
                    name=pat.name,
                    created_at=pat.created_at,
                    revoked_at=pat.revoked_at,
                )
                for pat, user in result.all()
            ],
            total=total,
            page=effective_page,
            page_size=page_size,
        )

    async def get_pat(self, pat_id: UUID) -> PATResponse | None:
        """Return PAT metadata by ID without mutating it."""

        result = await self._session.execute(
            select(PersonalAccessToken, User)
            .join(User, PersonalAccessToken.user_id == User.id)
            .where(PersonalAccessToken.id == pat_id)
        )
        row = result.first()
        if row is None:
            return None

        pat, user = row
        return PATResponse(
            id=pat.id,
            username=user.username,
            name=pat.name,
            created_at=pat.created_at,
            revoked_at=pat.revoked_at,
        )

    async def revoke_pat(
        self,
        pat_id: UUID,
        *,
        commit: bool = True,
    ) -> PATResponse | None:
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
            await self._session.flush()
            if commit:
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
        if not user.is_active:
            raise AuthenticationError("PAT owner is inactive.")

        return TokenPrincipalResponse(
            subject=user.username,
            username=user.username,
            token_type="pat",
            claims={
                "sub": user.username,
                "pat_id": str(pat.id),
                "name": pat.name,
                "roles": _normalized_roles(getattr(user, "roles", [])),
            },
        )

    def _validate_jwt(self, token: str) -> TokenPrincipalResponse:
        parts = token.split(".")
        if len(parts) != 3:
            raise AuthenticationError("JWT must contain three segments.")

        header_segment, payload_segment, signature_segment = parts
        try:
            header = json.loads(_b64decode_json(header_segment))
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
            raise AuthenticationError("Malformed JWT header.") from exc
        if not isinstance(header, dict):
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
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
            raise AuthenticationError("Malformed JWT payload.") from exc
        if not isinstance(claims, dict):
            raise AuthenticationError("Malformed JWT payload.")
        now_ts = datetime.now(UTC).timestamp()
        exp = _numeric_date(claims.get("exp"))
        if exp is None or exp <= now_ts:
            raise AuthenticationError("JWT is expired.")

        nbf = _numeric_date(claims.get("nbf"))
        if nbf is not None and nbf > now_ts:
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
            username=_jwt_username(claims),
            token_type="jwt",
            claims=claims,
        )

    async def sync_jwt_user_roles(
        self,
        principal: TokenPrincipalResponse,
        *,
        commit: bool = True,
    ) -> None:
        if principal.token_type != "jwt":
            return

        username = principal.username or principal.subject
        result = await self._session.execute(select(User).where(User.username == username))
        user = result.scalar_one_or_none()
        if user is None:
            return

        roles = _normalized_roles(principal.claims.get("roles"))
        if _normalized_roles(user.roles) == roles:
            return

        user.roles = roles
        await self._session.flush()
        if commit:
            await self._session.commit()

    async def _get_existing_user(self, *, username: str) -> User:
        result = await self._session.execute(select(User).where(User.username == username))
        user = result.scalar_one_or_none()
        if user is None:
            raise UserNotFoundError(f"User '{username}' not found.")
        return user


def load_jwt_settings() -> JWTSettings:
    """Load JWT settings from environment variables."""

    secret = (os.getenv("ACCESS_CONTROL_JWT_SECRET") or "").strip()
    if not secret:
        raise JWTConfigurationError("ACCESS_CONTROL_JWT_SECRET must be configured.")
    issuer = (os.getenv("ACCESS_CONTROL_JWT_ISSUER") or "").strip() or None
    audience = (os.getenv("ACCESS_CONTROL_JWT_AUDIENCE") or "").strip() or None
    return JWTSettings(secret=secret, issuer=issuer, audience=audience)


def resolve_jwt_settings(app_state: Any) -> JWTSettings:
    """Resolve JWT settings or raise a configuration error."""

    config_error = getattr(app_state, "jwt_settings_error", None)
    if isinstance(config_error, str) and config_error:
        raise JWTConfigurationError(config_error)

    settings = getattr(app_state, "jwt_settings", None)
    if isinstance(settings, JWTSettings):
        return settings

    raise JWTConfigurationError("JWT settings are not configured.")


def _jwt_username(claims: dict[str, Any]) -> str | None:
    for key in _USERNAME_CLAIM_KEYS:
        value = claims.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _normalized_roles(raw_roles: object) -> list[str]:
    if isinstance(raw_roles, str):
        values = [raw_roles]
    elif isinstance(raw_roles, list):
        values = raw_roles
    else:
        return []

    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            continue
        role = value.strip().lower()
        if not role or role in seen:
            continue
        seen.add(role)
        normalized.append(role)
    return normalized


def build_service_jwt(
    *,
    subject: str = "tool-compiler-control-plane",
    roles: list[str] | None = None,
    jwt_settings: JWTSettings | None = None,
    lifetime_seconds: int = 300,
) -> str:
    """Mint a short-lived HS256 JWT for internal control-plane calls."""

    settings = jwt_settings or load_jwt_settings()
    now = datetime.now(UTC)
    claims: dict[str, object] = {
        "sub": subject,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=lifetime_seconds)).timestamp()),
        "roles": roles or ["admin"],
    }
    if settings.issuer is not None:
        claims["iss"] = settings.issuer
    if settings.audience is not None:
        claims["aud"] = settings.audience

    header_segment = _b64encode_json({"alg": "HS256", "typ": "JWT"})
    payload_segment = _b64encode_json(claims)
    signature = hmac.new(
        settings.secret.encode("utf-8"),
        f"{header_segment}.{payload_segment}".encode(),
        hashlib.sha256,
    ).digest()
    signature_segment = base64.urlsafe_b64encode(signature).decode("utf-8").rstrip("=")
    return f"{header_segment}.{payload_segment}.{signature_segment}"


def _generate_pat() -> str:
    return f"{_PAT_PREFIX}{secrets.token_urlsafe(32)}"


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def hash_token_value(token: str) -> str:
    """Expose PAT hashing for gateway binding and reconciliation."""

    return _hash_token(token)


def _b64decode_json(segment: str) -> str:
    return _b64decode_bytes(segment).decode("utf-8")


def _b64encode_json(payload: dict[str, object]) -> str:
    return (
        base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
        .decode("utf-8")
        .rstrip("=")
    )


def _b64decode_bytes(segment: str) -> bytes:
    padding = "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(segment + padding)


def _audience_matches(value: Any, expected_audience: str) -> bool:
    if isinstance(value, str):
        return value == expected_audience
    if isinstance(value, list):
        return expected_audience in value
    return False


def _numeric_date(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)
