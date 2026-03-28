"""Unit tests for authn service — JWT validation and helper functions.

These tests cover the pure/near-pure functions that don't require a database.
DB-backed PAT methods are covered in integration tests.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from apps.access_control.authn.service import (
    AuthenticationError,
    AuthnService,
    JWTSettings,
    _audience_matches,
    _b64decode_bytes,
    _b64decode_json,
    _generate_pat,
    _hash_token,
    hash_token_value,
    load_jwt_settings,
)

# --- JWT construction helpers ---

_SECRET = "test-secret-key"


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _make_jwt(
    claims: dict[str, object],
    *,
    secret: str = _SECRET,
    alg: str = "HS256",
) -> str:
    header = _b64encode(json.dumps({"alg": alg, "typ": "JWT"}).encode())
    payload = _b64encode(json.dumps(claims).encode())
    signing_input = f"{header}.{payload}".encode()
    sig = hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
    return f"{header}.{payload}.{_b64encode(sig)}"


def _valid_claims(**overrides: object) -> dict[str, object]:
    now = int(datetime.now(UTC).timestamp())
    base: dict[str, object] = {
        "sub": "test-user",
        "exp": now + 3600,
        "iat": now,
    }
    base.update(overrides)
    return base


def _make_service(secret: str = _SECRET, **kwargs: object) -> AuthnService:
    session = AsyncMock()
    return AuthnService(
        session, jwt_settings=JWTSettings(secret=secret, **kwargs)  # type: ignore[arg-type]
    )


# --- Tests ---


class TestB64Helpers:
    def test_b64decode_json(self) -> None:
        raw = json.dumps({"hello": "world"})
        encoded = _b64encode(raw.encode())
        assert json.loads(_b64decode_json(encoded)) == {"hello": "world"}

    def test_b64decode_bytes(self) -> None:
        data = b"\x00\x01\x02\xff"
        encoded = _b64encode(data)
        assert _b64decode_bytes(encoded) == data

    def test_handles_missing_padding(self) -> None:
        raw = b"test data"
        encoded = base64.urlsafe_b64encode(raw).decode().rstrip("=")
        assert _b64decode_bytes(encoded) == raw


class TestAudienceMatches:
    def test_string_match(self) -> None:
        assert _audience_matches("my-app", "my-app") is True

    def test_string_no_match(self) -> None:
        assert _audience_matches("other-app", "my-app") is False

    def test_list_match(self) -> None:
        assert _audience_matches(["a", "b", "my-app"], "my-app") is True

    def test_list_no_match(self) -> None:
        assert _audience_matches(["a", "b"], "my-app") is False

    def test_none_returns_false(self) -> None:
        assert _audience_matches(None, "my-app") is False

    def test_int_returns_false(self) -> None:
        assert _audience_matches(42, "my-app") is False


class TestGeneratePat:
    def test_starts_with_prefix(self) -> None:
        token = _generate_pat()
        assert token.startswith("pat_")

    def test_unique_each_call(self) -> None:
        tokens = {_generate_pat() for _ in range(10)}
        assert len(tokens) == 10

    def test_reasonable_length(self) -> None:
        token = _generate_pat()
        assert len(token) > 20


class TestHashToken:
    def test_deterministic(self) -> None:
        assert _hash_token("my-token") == _hash_token("my-token")

    def test_sha256_hex(self) -> None:
        expected = hashlib.sha256(b"my-token").hexdigest()
        assert _hash_token("my-token") == expected

    def test_hash_token_value_same_as_internal(self) -> None:
        assert hash_token_value("abc123") == _hash_token("abc123")


class TestLoadJwtSettings:
    def test_defaults(self) -> None:
        settings = load_jwt_settings()
        assert isinstance(settings.secret, str)

    def test_from_env(self) -> None:
        env = {
            "ACCESS_CONTROL_JWT_SECRET": "my-secret",
            "ACCESS_CONTROL_JWT_ISSUER": "my-issuer",
            "ACCESS_CONTROL_JWT_AUDIENCE": "my-audience",
        }
        with pytest.MonkeyPatch.context() as mp:
            for k, v in env.items():
                mp.setenv(k, v)
            settings = load_jwt_settings()
        assert settings.secret == "my-secret"
        assert settings.issuer == "my-issuer"
        assert settings.audience == "my-audience"


class TestJWTSettingsFrozen:
    def test_frozen(self) -> None:
        settings = JWTSettings(secret="s")
        with pytest.raises(AttributeError):
            settings.secret = "other"  # type: ignore[misc]


class TestValidateJwtSuccess:
    def test_valid_jwt(self) -> None:
        svc = _make_service()
        token = _make_jwt(_valid_claims())
        result = svc._validate_jwt(token)
        assert result.subject == "test-user"
        assert result.token_type == "jwt"

    def test_claims_included(self) -> None:
        svc = _make_service()
        claims = _valid_claims(custom_field="hello")
        token = _make_jwt(claims)
        result = svc._validate_jwt(token)
        assert result.claims["custom_field"] == "hello"

    def test_with_nbf(self) -> None:
        svc = _make_service()
        now = int(datetime.now(UTC).timestamp())
        token = _make_jwt(_valid_claims(nbf=now - 60))
        result = svc._validate_jwt(token)
        assert result.subject == "test-user"


class TestValidateJwtIssuerAndAudience:
    def test_issuer_validation_pass(self) -> None:
        svc = _make_service(issuer="my-issuer")
        token = _make_jwt(_valid_claims(iss="my-issuer"))
        result = svc._validate_jwt(token)
        assert result.subject == "test-user"

    def test_issuer_validation_fail(self) -> None:
        svc = _make_service(issuer="my-issuer")
        token = _make_jwt(_valid_claims(iss="other-issuer"))
        with pytest.raises(AuthenticationError, match="issuer"):
            svc._validate_jwt(token)

    def test_audience_validation_pass(self) -> None:
        svc = _make_service(audience="my-audience")
        token = _make_jwt(_valid_claims(aud="my-audience"))
        result = svc._validate_jwt(token)
        assert result.subject == "test-user"

    def test_audience_validation_fail(self) -> None:
        svc = _make_service(audience="my-audience")
        token = _make_jwt(_valid_claims(aud="wrong-audience"))
        with pytest.raises(AuthenticationError, match="audience"):
            svc._validate_jwt(token)

    def test_audience_list_pass(self) -> None:
        svc = _make_service(audience="my-audience")
        token = _make_jwt(_valid_claims(aud=["a", "my-audience"]))
        result = svc._validate_jwt(token)
        assert result.subject == "test-user"


class TestValidateJwtErrors:
    def test_not_three_segments(self) -> None:
        svc = _make_service()
        with pytest.raises(AuthenticationError, match="three segments"):
            svc._validate_jwt("only.two")

    def test_unsupported_algorithm(self) -> None:
        svc = _make_service()
        token = _make_jwt(_valid_claims(), alg="RS256")
        with pytest.raises(AuthenticationError, match="Unsupported JWT algorithm"):
            svc._validate_jwt(token)

    def test_invalid_signature(self) -> None:
        svc = _make_service()
        token = _make_jwt(_valid_claims(), secret="wrong-secret")
        with pytest.raises(AuthenticationError, match="signature"):
            svc._validate_jwt(token)

    def test_expired_jwt(self) -> None:
        svc = _make_service()
        past = int((datetime.now(UTC) - timedelta(hours=1)).timestamp())
        token = _make_jwt(_valid_claims(exp=past))
        with pytest.raises(AuthenticationError, match="expired"):
            svc._validate_jwt(token)

    def test_not_active_yet(self) -> None:
        svc = _make_service()
        future = int((datetime.now(UTC) + timedelta(hours=1)).timestamp())
        token = _make_jwt(_valid_claims(nbf=future))
        with pytest.raises(AuthenticationError, match="not active"):
            svc._validate_jwt(token)

    def test_missing_subject(self) -> None:
        svc = _make_service()
        claims = _valid_claims()
        del claims["sub"]
        token = _make_jwt(claims)
        with pytest.raises(AuthenticationError, match="subject"):
            svc._validate_jwt(token)

    def test_empty_subject(self) -> None:
        svc = _make_service()
        token = _make_jwt(_valid_claims(sub=""))
        with pytest.raises(AuthenticationError, match="subject"):
            svc._validate_jwt(token)

    def test_non_string_subject(self) -> None:
        svc = _make_service()
        token = _make_jwt(_valid_claims(sub=42))
        with pytest.raises(AuthenticationError, match="subject"):
            svc._validate_jwt(token)

    def test_exp_not_int(self) -> None:
        svc = _make_service()
        token = _make_jwt(_valid_claims(exp="not-a-number"))
        with pytest.raises(AuthenticationError, match="expired"):
            svc._validate_jwt(token)


class TestValidateTokenDispatch:
    @pytest.mark.asyncio
    async def test_jwt_token_routed(self) -> None:
        svc = _make_service()
        token = _make_jwt(_valid_claims())
        result = await svc.validate_token(token)
        assert result.token_type == "jwt"

    @pytest.mark.asyncio
    async def test_pat_token_routed_to_validate_pat(self) -> None:
        """PAT tokens starting with pat_ are routed to _validate_pat which
        hits the DB. With a mock session returning None, AuthenticationError fires."""
        from unittest.mock import MagicMock

        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.first.return_value = None
        session.execute.return_value = mock_result
        svc = AuthnService(session, jwt_settings=JWTSettings(secret=_SECRET))
        with pytest.raises(AuthenticationError, match="PAT is invalid"):
            await svc.validate_token("pat_invalid_token_for_unit_test")


# Additional tests to cover uncovered lines in authn/service.py

class TestCreatePat:
    pass


class TestRevokePat:
    pass


class TestValidatePat:
    pass


class TestValidateJwt:
    def test_malformed_header_json_decode_error(self):
        """Test line 157: malformed JWT header JSON."""
        svc = _make_service()
        
        # Create token with invalid JSON in header
        invalid_header = _b64encode(b"not-json")
        payload = _b64encode(json.dumps(_valid_claims()).encode())
        signing_input = f"{invalid_header}.{payload}".encode()
        sig = hmac.new(_SECRET.encode(), signing_input, hashlib.sha256).digest()
        token = f"{invalid_header}.{payload}.{_b64encode(sig)}"
        
        with pytest.raises(AuthenticationError, match="Malformed JWT header"):
            svc._validate_jwt(token)

    def test_malformed_header_unicode_decode_error(self):
        """Test line 157: malformed JWT header unicode decode."""
        svc = _make_service()
        
        # Create token with invalid UTF-8 in header
        invalid_header = _b64encode(b'\xff\xfe')  # Invalid UTF-8
        payload = _b64encode(json.dumps(_valid_claims()).encode())
        signing_input = f"{invalid_header}.{payload}".encode()
        sig = hmac.new(_SECRET.encode(), signing_input, hashlib.sha256).digest()
        token = f"{invalid_header}.{payload}.{_b64encode(sig)}"
        
        with pytest.raises(AuthenticationError, match="Malformed JWT header"):
            svc._validate_jwt(token)

    def test_malformed_signature_exception(self):
        """Test lines 170-171: malformed JWT signature encoding."""
        svc = _make_service()
        
        header = _b64encode(json.dumps({"alg": "HS256"}).encode())
        payload = _b64encode(json.dumps(_valid_claims()).encode())
        invalid_signature = "invalid@#$%^&*()base64"  # Invalid base64 that will cause exception
        token = f"{header}.{payload}.{invalid_signature}"
        
        with pytest.raises(AuthenticationError, match="Malformed JWT signature encoding"):
            svc._validate_jwt(token)

    def test_malformed_payload_json_decode_error(self):
        """Test lines 177-178: malformed JWT payload JSON."""
        svc = _make_service()
        
        header = _b64encode(json.dumps({"alg": "HS256"}).encode())
        invalid_payload = _b64encode(b"not-json")
        signing_input = f"{header}.{invalid_payload}".encode()
        sig = hmac.new(_SECRET.encode(), signing_input, hashlib.sha256).digest()
        token = f"{header}.{invalid_payload}.{_b64encode(sig)}"
        
        with pytest.raises(AuthenticationError, match="Malformed JWT payload"):
            svc._validate_jwt(token)

    def test_malformed_payload_unicode_decode_error(self):
        """Test lines 177-178: malformed JWT payload unicode decode."""
        svc = _make_service()
        
        header = _b64encode(json.dumps({"alg": "HS256"}).encode())
        invalid_payload = _b64encode(b'\xff\xfe')  # Invalid UTF-8  
        signing_input = f"{header}.{invalid_payload}".encode()
        sig = hmac.new(_SECRET.encode(), signing_input, hashlib.sha256).digest()
        token = f"{header}.{invalid_payload}.{_b64encode(sig)}"
        
        with pytest.raises(AuthenticationError, match="Malformed JWT payload"):
            svc._validate_jwt(token)


class TestGetOrCreateUser:

    async def test_create_new_user(self):
        """Test lines 217-221: _get_or_create_user creates new user."""
        from unittest.mock import MagicMock
        
        session = AsyncMock()
        
        # Mock no existing user
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        session.execute.return_value = mock_result
        
        svc = AuthnService(session, jwt_settings=JWTSettings(secret=_SECRET))
        
        result = await svc._get_or_create_user(username="bob", email="bob@example.com")
        
        # Should add new user and commit/refresh
        session.add.assert_called_once()
        session.commit.assert_called_once()
        session.refresh.assert_called_once()


class TestLoadJwtSettings:
    def test_load_jwt_settings_non_dev_environment_no_secret(self):
        """Test lines 231-233: load_jwt_settings raises error in non-dev env without secret."""
        import os
        from unittest.mock import patch
        from apps.access_control.authn.service import load_jwt_settings
        
        with patch.dict(os.environ, {"ENV": "production"}, clear=True):
            with pytest.raises(RuntimeError, match="ACCESS_CONTROL_JWT_SECRET must be set"):
                load_jwt_settings()

    def test_load_jwt_settings_dev_environment_uses_default(self):
        """Test lines 229-234: load_jwt_settings uses dev secret in dev environment."""
        import os
        from unittest.mock import patch
        from apps.access_control.authn.service import load_jwt_settings
        
        with patch.dict(os.environ, {"ENV": "dev"}, clear=True):
            settings = load_jwt_settings()
            assert settings.secret == "dev-secret"
