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
    JWTConfigurationError,
    JWTSettings,
    UserNotFoundError,
    _audience_matches,
    _b64decode_bytes,
    _b64decode_json,
    _generate_pat,
    _hash_token,
    build_service_jwt,
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
        session,
        jwt_settings=JWTSettings(secret=secret, **kwargs),
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


class TestLoadJwtSettingsEnvBehavior:
    def test_defaults(self) -> None:
        with pytest.MonkeyPatch.context() as mp:
            mp.delenv("ACCESS_CONTROL_JWT_SECRET", raising=False)
            mp.delenv("ACCESS_CONTROL_JWT_ISSUER", raising=False)
            mp.delenv("ACCESS_CONTROL_JWT_AUDIENCE", raising=False)
            with pytest.raises(
                JWTConfigurationError,
                match="ACCESS_CONTROL_JWT_SECRET must be configured",
            ):
                load_jwt_settings()

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
            settings.secret = "other"


class TestValidateJwtSuccess:
    def test_valid_jwt(self) -> None:
        svc = _make_service()
        token = _make_jwt(_valid_claims())
        result = svc._validate_jwt(token)
        assert result.subject == "test-user"
        assert result.username is None
        assert result.token_type == "jwt"

    def test_claims_included(self) -> None:
        svc = _make_service()
        claims = _valid_claims(custom_field="hello")
        token = _make_jwt(claims)
        result = svc._validate_jwt(token)
        assert result.claims["custom_field"] == "hello"

    def test_prefers_platform_username_claim_over_subject(self) -> None:
        svc = _make_service()
        claims = _valid_claims(
            sub="alice@example.com",
            preferred_username="alice",
            username="ignored",
        )
        token = _make_jwt(claims)
        result = svc._validate_jwt(token)
        assert result.subject == "alice@example.com"
        assert result.username == "alice"

    def test_with_nbf(self) -> None:
        svc = _make_service()
        now = int(datetime.now(UTC).timestamp())
        token = _make_jwt(_valid_claims(nbf=now - 60))
        result = svc._validate_jwt(token)
        assert result.subject == "test-user"

    def test_exp_float_is_accepted(self) -> None:
        svc = _make_service()
        future = datetime.now(UTC).timestamp() + 3600.5
        token = _make_jwt(_valid_claims(exp=future))
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

    def test_future_float_nbf_is_rejected(self) -> None:
        svc = _make_service()
        future = datetime.now(UTC).timestamp() + 3600.5
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


class TestBuildServiceJwt:
    def test_includes_issuer_claim(self) -> None:
        settings = JWTSettings(secret=_SECRET, issuer="my-issuer")
        token = build_service_jwt(jwt_settings=settings)
        claims = json.loads(_b64decode_json(token.split(".")[1]))
        assert claims["iss"] == "my-issuer"

    def test_includes_audience_claim(self) -> None:
        settings = JWTSettings(secret=_SECRET, audience="my-audience")
        token = build_service_jwt(jwt_settings=settings)
        claims = json.loads(_b64decode_json(token.split(".")[1]))
        assert claims["aud"] == "my-audience"

    def test_includes_both_issuer_and_audience(self) -> None:
        settings = JWTSettings(secret=_SECRET, issuer="iss", audience="aud")
        token = build_service_jwt(jwt_settings=settings)
        claims = json.loads(_b64decode_json(token.split(".")[1]))
        assert claims["iss"] == "iss"
        assert claims["aud"] == "aud"

    def test_omits_issuer_when_none(self) -> None:
        settings = JWTSettings(secret=_SECRET)
        token = build_service_jwt(jwt_settings=settings)
        claims = json.loads(_b64decode_json(token.split(".")[1]))
        assert "iss" not in claims

    def test_omits_audience_when_none(self) -> None:
        settings = JWTSettings(secret=_SECRET)
        token = build_service_jwt(jwt_settings=settings)
        claims = json.loads(_b64decode_json(token.split(".")[1]))
        assert "aud" not in claims

    def test_result_is_valid_jwt(self) -> None:
        settings = JWTSettings(secret=_SECRET, issuer="iss", audience="aud")
        token = build_service_jwt(jwt_settings=settings, subject="svc-user", roles=["reader"])
        svc = _make_service(issuer="iss", audience="aud")
        result = svc._validate_jwt(token)
        assert result.subject == "svc-user"
        assert result.claims["roles"] == ["reader"]
        assert result.claims["iss"] == "iss"
        assert result.claims["aud"] == "aud"


class TestCreatePat:
    @pytest.mark.asyncio
    async def test_create_pat_returns_response(self) -> None:
        from types import SimpleNamespace
        from uuid import uuid4

        session = AsyncMock()
        svc = AuthnService(session, jwt_settings=JWTSettings(secret=_SECRET))

        fake_user = SimpleNamespace(id=uuid4(), username="alice", email="alice@test.com")
        svc._get_existing_user = AsyncMock(return_value=fake_user)

        pat_id = uuid4()
        created_at = datetime.now(UTC)

        def fake_refresh(record):
            record.id = pat_id
            record.created_at = created_at
            record.revoked_at = None

        session.refresh.side_effect = fake_refresh

        result = await svc.create_pat(username="alice", name="my-token")

        assert result.username == "alice"
        assert result.name == "my-token"
        assert result.id == pat_id
        assert result.created_at == created_at
        assert result.revoked_at is None
        assert result.token.startswith("pat_")
        session.add.assert_called_once()
        session.flush.assert_awaited_once()
        session.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_create_pat_without_commit(self) -> None:
        from types import SimpleNamespace
        from uuid import uuid4

        session = AsyncMock()
        svc = AuthnService(session, jwt_settings=JWTSettings(secret=_SECRET))

        fake_user = SimpleNamespace(id=uuid4(), username="bob", email=None)
        svc._get_existing_user = AsyncMock(return_value=fake_user)

        def fake_refresh(record):
            record.id = uuid4()
            record.created_at = datetime.now(UTC)
            record.revoked_at = None

        session.refresh.side_effect = fake_refresh

        result = await svc.create_pat(username="bob", name="tok", commit=False)

        assert result.username == "bob"
        session.commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_create_pat_requires_existing_user(self) -> None:
        session = AsyncMock()
        svc = AuthnService(session, jwt_settings=JWTSettings(secret=_SECRET))
        svc._get_existing_user = AsyncMock(side_effect=UserNotFoundError("User 'ghost' not found."))

        with pytest.raises(UserNotFoundError, match="ghost"):
            await svc.create_pat(username="ghost", name="tok")

        session.add.assert_not_called()
        session.flush.assert_not_awaited()


class TestListPats:
    @pytest.mark.asyncio
    async def test_list_pats_returns_responses(self) -> None:
        from types import SimpleNamespace
        from unittest.mock import MagicMock
        from uuid import uuid4

        session = AsyncMock()
        svc = AuthnService(session, jwt_settings=JWTSettings(secret=_SECRET))

        now = datetime.now(UTC)
        pat1 = SimpleNamespace(id=uuid4(), name="tok1", created_at=now, revoked_at=None)
        pat2 = SimpleNamespace(id=uuid4(), name="tok2", created_at=now, revoked_at=now)
        user = SimpleNamespace(username="alice", is_active=True)

        count_result = MagicMock()
        count_result.scalar_one.return_value = 2
        list_result = MagicMock()
        list_result.all.return_value = [(pat1, user), (pat2, user)]
        session.execute.side_effect = [count_result, list_result]

        result = await svc.list_pats(username="alice")
        assert result.total == 2
        assert result.page == 1
        assert result.page_size == 100
        assert len(result.items) == 2
        assert result.items[0].name == "tok1"
        assert result.items[0].revoked_at is None
        assert result.items[1].name == "tok2"
        assert result.items[1].revoked_at == now

    @pytest.mark.asyncio
    async def test_list_pats_empty(self) -> None:
        from unittest.mock import MagicMock

        session = AsyncMock()
        svc = AuthnService(session, jwt_settings=JWTSettings(secret=_SECRET))

        count_result = MagicMock()
        count_result.scalar_one.return_value = 0
        list_result = MagicMock()
        list_result.all.return_value = []
        session.execute.side_effect = [count_result, list_result]

        result = await svc.list_pats(username="nobody")
        assert result.items == []
        assert result.total == 0
        assert result.page == 1

    @pytest.mark.asyncio
    async def test_list_pats_clamps_page_to_last_page(self) -> None:
        from types import SimpleNamespace
        from unittest.mock import MagicMock
        from uuid import uuid4

        session = AsyncMock()
        svc = AuthnService(session, jwt_settings=JWTSettings(secret=_SECRET))

        now = datetime.now(UTC)
        user = SimpleNamespace(username="alice", is_active=True)
        pat = SimpleNamespace(id=uuid4(), name="tok-last", created_at=now, revoked_at=None)

        count_result = MagicMock()
        count_result.scalar_one.return_value = 101
        list_result = MagicMock()
        list_result.all.return_value = [(pat, user)]
        session.execute.side_effect = [count_result, list_result]

        result = await svc.list_pats(username="alice", page=9, page_size=100)

        assert result.total == 101
        assert result.page == 2
        assert result.page_size == 100
        assert [item.name for item in result.items] == ["tok-last"]


class TestGetPat:
    @pytest.mark.asyncio
    async def test_get_pat_found(self) -> None:
        from types import SimpleNamespace
        from unittest.mock import MagicMock
        from uuid import uuid4

        session = AsyncMock()
        svc = AuthnService(session, jwt_settings=JWTSettings(secret=_SECRET))

        pat_id = uuid4()
        now = datetime.now(UTC)
        pat = SimpleNamespace(id=pat_id, name="tok", created_at=now, revoked_at=None)
        user = SimpleNamespace(username="alice", is_active=True)

        mock_result = MagicMock()
        mock_result.first.return_value = (pat, user)
        session.execute.return_value = mock_result

        result = await svc.get_pat(pat_id)
        assert result is not None
        assert result.id == pat_id
        assert result.username == "alice"
        assert result.name == "tok"

    @pytest.mark.asyncio
    async def test_get_pat_not_found(self) -> None:
        from unittest.mock import MagicMock
        from uuid import uuid4

        session = AsyncMock()
        svc = AuthnService(session, jwt_settings=JWTSettings(secret=_SECRET))

        mock_result = MagicMock()
        mock_result.first.return_value = None
        session.execute.return_value = mock_result

        result = await svc.get_pat(uuid4())
        assert result is None


class TestRevokePat:
    @pytest.mark.asyncio
    async def test_revoke_active_pat(self) -> None:
        from types import SimpleNamespace
        from unittest.mock import MagicMock
        from uuid import uuid4

        session = AsyncMock()
        svc = AuthnService(session, jwt_settings=JWTSettings(secret=_SECRET))

        pat_id = uuid4()
        now = datetime.now(UTC)
        pat = SimpleNamespace(id=pat_id, name="tok", created_at=now, revoked_at=None)
        user = SimpleNamespace(username="alice")

        mock_result = MagicMock()
        mock_result.first.return_value = (pat, user)
        session.execute.return_value = mock_result

        def fake_refresh(record):
            pass  # revoked_at already set by the method

        session.refresh.side_effect = fake_refresh

        result = await svc.revoke_pat(pat_id)
        assert result is not None
        assert result.id == pat_id
        assert result.revoked_at is not None
        session.flush.assert_awaited_once()
        session.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_revoke_already_revoked_pat(self) -> None:
        from types import SimpleNamespace
        from unittest.mock import MagicMock
        from uuid import uuid4

        session = AsyncMock()
        svc = AuthnService(session, jwt_settings=JWTSettings(secret=_SECRET))

        pat_id = uuid4()
        now = datetime.now(UTC)
        pat = SimpleNamespace(id=pat_id, name="tok", created_at=now, revoked_at=now)
        user = SimpleNamespace(username="alice")

        mock_result = MagicMock()
        mock_result.first.return_value = (pat, user)
        session.execute.return_value = mock_result

        result = await svc.revoke_pat(pat_id)
        assert result is not None
        assert result.revoked_at == now
        # Should not flush/commit since already revoked
        session.flush.assert_not_awaited()
        session.commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_revoke_pat_not_found(self) -> None:
        from unittest.mock import MagicMock
        from uuid import uuid4

        session = AsyncMock()
        svc = AuthnService(session, jwt_settings=JWTSettings(secret=_SECRET))

        mock_result = MagicMock()
        mock_result.first.return_value = None
        session.execute.return_value = mock_result

        result = await svc.revoke_pat(uuid4())
        assert result is None

    @pytest.mark.asyncio
    async def test_revoke_active_pat_without_commit(self) -> None:
        from types import SimpleNamespace
        from unittest.mock import MagicMock
        from uuid import uuid4

        session = AsyncMock()
        svc = AuthnService(session, jwt_settings=JWTSettings(secret=_SECRET))

        pat_id = uuid4()
        now = datetime.now(UTC)
        pat = SimpleNamespace(id=pat_id, name="tok", created_at=now, revoked_at=None)
        user = SimpleNamespace(username="alice")

        mock_result = MagicMock()
        mock_result.first.return_value = (pat, user)
        session.execute.return_value = mock_result
        session.refresh.side_effect = lambda record: None

        result = await svc.revoke_pat(pat_id, commit=False)
        assert result is not None
        assert result.revoked_at is not None
        session.flush.assert_awaited_once()
        session.commit.assert_not_awaited()


class TestValidatePat:
    @pytest.mark.asyncio
    async def test_validate_valid_active_pat(self) -> None:
        from types import SimpleNamespace
        from unittest.mock import MagicMock
        from uuid import uuid4

        session = AsyncMock()
        svc = AuthnService(session, jwt_settings=JWTSettings(secret=_SECRET))

        pat_id = uuid4()
        pat = SimpleNamespace(
            id=pat_id,
            name="my-pat",
            revoked_at=None,
            token_hash=_hash_token("pat_abc123"),
        )
        user = SimpleNamespace(username="alice", is_active=True, roles=[" Admin ", "viewer"])

        mock_result = MagicMock()
        mock_result.first.return_value = (pat, user)
        session.execute.return_value = mock_result

        result = await svc._validate_pat("pat_abc123")
        assert result.subject == "alice"
        assert result.username == "alice"
        assert result.token_type == "pat"
        assert result.claims["sub"] == "alice"
        assert result.claims["pat_id"] == str(pat_id)
        assert result.claims["name"] == "my-pat"
        assert result.claims["roles"] == ["admin", "viewer"]

    @pytest.mark.asyncio
    async def test_validate_revoked_pat_raises(self) -> None:
        from types import SimpleNamespace
        from unittest.mock import MagicMock
        from uuid import uuid4

        session = AsyncMock()
        svc = AuthnService(session, jwt_settings=JWTSettings(secret=_SECRET))

        pat = SimpleNamespace(id=uuid4(), name="tok", revoked_at=datetime.now(UTC), token_hash="x")
        user = SimpleNamespace(username="alice")

        mock_result = MagicMock()
        mock_result.first.return_value = (pat, user)
        session.execute.return_value = mock_result

        with pytest.raises(AuthenticationError, match="revoked"):
            await svc._validate_pat("pat_revoked")


class TestValidateJwt:
    def test_non_object_header_raises_authentication_error(self):
        svc = _make_service()

        invalid_header = _b64encode(b"[]")
        payload = _b64encode(json.dumps(_valid_claims()).encode())
        signing_input = f"{invalid_header}.{payload}".encode()
        sig = hmac.new(_SECRET.encode(), signing_input, hashlib.sha256).digest()
        token = f"{invalid_header}.{payload}.{_b64encode(sig)}"

        with pytest.raises(AuthenticationError, match="Malformed JWT header"):
            svc._validate_jwt(token)


class TestSyncJwtUserRoles:
    @pytest.mark.asyncio
    async def test_updates_existing_user_roles(self) -> None:
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        session = AsyncMock()
        svc = AuthnService(session, jwt_settings=JWTSettings(secret=_SECRET))
        user = SimpleNamespace(username="alice", roles=["viewer"])

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = user
        session.execute.return_value = mock_result

        principal = svc._validate_jwt(
            _make_jwt(_valid_claims(sub="alice", roles=[" Admin ", "admin"]))
        )
        await svc.sync_jwt_user_roles(principal)

        assert user.roles == ["admin"]
        session.flush.assert_awaited_once()
        session.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_skips_missing_local_user(self) -> None:
        from unittest.mock import MagicMock

        session = AsyncMock()
        svc = AuthnService(session, jwt_settings=JWTSettings(secret=_SECRET))

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        session.execute.return_value = mock_result

        principal = svc._validate_jwt(_make_jwt(_valid_claims(sub="ghost", roles=["admin"])))
        await svc.sync_jwt_user_roles(principal)

        session.flush.assert_not_awaited()
        session.commit.assert_not_awaited()

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
        invalid_header = _b64encode(b"\xff\xfe")  # Invalid UTF-8
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
        invalid_payload = _b64encode(b"\xff\xfe")  # Invalid UTF-8
        signing_input = f"{header}.{invalid_payload}".encode()
        sig = hmac.new(_SECRET.encode(), signing_input, hashlib.sha256).digest()
        token = f"{header}.{invalid_payload}.{_b64encode(sig)}"

        with pytest.raises(AuthenticationError, match="Malformed JWT payload"):
            svc._validate_jwt(token)

    def test_non_object_payload_raises_authentication_error(self):
        svc = _make_service()

        header = _b64encode(json.dumps({"alg": "HS256"}).encode())
        invalid_payload = _b64encode(b"[]")
        signing_input = f"{header}.{invalid_payload}".encode()
        sig = hmac.new(_SECRET.encode(), signing_input, hashlib.sha256).digest()
        token = f"{header}.{invalid_payload}.{_b64encode(sig)}"

        with pytest.raises(AuthenticationError, match="Malformed JWT payload"):
            svc._validate_jwt(token)


class TestGetExistingUser:
    async def test_returns_existing_user(self):
        """Existing users should be returned without mutation."""
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        session = AsyncMock()
        existing_user = SimpleNamespace(id="user-id", username="bob", email="bob@example.com")

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = existing_user
        session.execute.return_value = mock_result

        svc = AuthnService(session, jwt_settings=JWTSettings(secret=_SECRET))

        result = await svc._get_existing_user(username="bob")

        assert result is existing_user
        session.add.assert_not_called()
        session.commit.assert_not_awaited()
        session.refresh.assert_not_awaited()

    async def test_raises_when_user_missing(self):
        """Unknown PAT owners should fail explicitly."""
        from unittest.mock import MagicMock

        session = AsyncMock()

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        session.execute.return_value = mock_result

        svc = AuthnService(session, jwt_settings=JWTSettings(secret=_SECRET))

        with pytest.raises(UserNotFoundError, match="ghost"):
            await svc._get_existing_user(username="ghost")


class TestLoadJwtSettings:
    def test_load_jwt_settings_non_dev_environment_no_secret(self):
        """Missing JWT secret always raises a configuration error."""
        import os
        from unittest.mock import patch

        from apps.access_control.authn.service import load_jwt_settings

        with patch.dict(os.environ, {"ENV": "production"}, clear=True):
            with pytest.raises(
                JWTConfigurationError,
                match="ACCESS_CONTROL_JWT_SECRET must be configured",
            ):
                load_jwt_settings()

    def test_load_jwt_settings_dev_environment_still_requires_secret(self):
        """Dev environments must no longer fall back to a known shared secret."""
        import os
        from unittest.mock import patch

        from apps.access_control.authn.service import load_jwt_settings

        with patch.dict(os.environ, {"ENV": "dev"}, clear=True):
            with pytest.raises(
                JWTConfigurationError,
                match="ACCESS_CONTROL_JWT_SECRET must be configured",
            ):
                load_jwt_settings()
