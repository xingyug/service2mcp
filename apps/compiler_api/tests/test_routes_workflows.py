"""Unit tests for apps/compiler_api/routes/workflows.py."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from apps.access_control.authn.models import TokenPrincipalResponse
from apps.compiler_api.routes.workflows import (
    ReviewNotesUpdate,
    TransitionRequest,
    get_workflow,
    get_workflow_history,
    save_review_notes,
    transition_workflow,
)


def _caller(
    subject: str = "alice",
    *,
    username: str | None = None,
    roles: list[str] | None = None,
) -> TokenPrincipalResponse:
    claims: dict[str, object] = {"sub": subject}
    if roles is not None:
        claims["roles"] = roles
    return TokenPrincipalResponse(
        subject=subject,
        username=username,
        token_type="jwt",
        claims=claims,
    )


def _make_record(
    *,
    state: str = "draft",
    history: list | None = None,
    review_notes: dict | None = None,
    tenant: str | None = None,
    environment: str | None = None,
) -> MagicMock:
    record = MagicMock()
    record.id = uuid4()
    record.service_id = "svc-1"
    record.version_number = 1
    record.tenant = tenant
    record.environment = environment
    record.state = state
    record.review_notes = review_notes
    record.history = history or []
    record.created_at = datetime(2025, 1, 1, tzinfo=UTC)
    record.updated_at = datetime(2025, 1, 1, tzinfo=UTC)
    return record


# ---------------------------------------------------------------------------
# GET workflow
# ---------------------------------------------------------------------------


class TestGetWorkflow:
    async def test_returns_existing_workflow(self) -> None:
        record = _make_record(state="in_review", tenant="team-a", environment="prod")
        session = AsyncMock()

        with (
            patch(
                "apps.compiler_api.routes.workflows._require_existing_service_version",
                return_value=None,
            ) as require_existing,
            patch(
                "apps.compiler_api.routes.workflows._get_or_create",
                return_value=record,
            ) as get_or_create,
        ):
            result = await get_workflow(
                "svc-1",
                1,
                tenant="team-a",
                environment="prod",
                session=session,
                _caller=_caller(),
            )

        assert result.state == "in_review"
        assert result.service_id == "svc-1"
        assert result.tenant == "team-a"
        assert result.environment == "prod"
        require_existing.assert_awaited_once_with(
            session,
            "svc-1",
            1,
            tenant="team-a",
            environment="prod",
        )
        get_or_create.assert_awaited_once_with(
            session,
            "svc-1",
            1,
            tenant="team-a",
            environment="prod",
        )

    async def test_creates_draft_when_missing(self) -> None:
        new_record = _make_record(state="draft")
        session = AsyncMock()

        with (
            patch(
                "apps.compiler_api.routes.workflows._require_existing_service_version",
                return_value=None,
            ),
            patch(
                "apps.compiler_api.routes.workflows._get_or_create",
                return_value=new_record,
            ),
        ):
            result = await get_workflow("svc-1", 1, session=session, _caller=_caller())

        assert result.state == "draft"
        session.commit.assert_awaited_once()


# ---------------------------------------------------------------------------
# POST transition
# ---------------------------------------------------------------------------


class TestTransitionWorkflow:
    async def test_valid_transition(self) -> None:
        record = _make_record(state="draft")
        session = AsyncMock()
        session.refresh = AsyncMock()

        with (
            patch(
                "apps.compiler_api.routes.workflows._require_existing_service_version",
                return_value=None,
            ),
            patch(
                "apps.compiler_api.routes.workflows._get_or_create",
                return_value=record,
            ),
        ):
            payload = TransitionRequest(to="submitted", actor="alice")
            result = await transition_workflow(
                "svc-1",
                1,
                payload,
                session=session,
                caller=_caller(subject="alice@example.com", username="alice"),
            )

        assert result.state == "submitted"
        session.commit.assert_awaited_once()

    async def test_invalid_transition_returns_409(self) -> None:
        record = _make_record(state="draft")
        session = AsyncMock()

        with (
            patch(
                "apps.compiler_api.routes.workflows._require_existing_service_version",
                return_value=None,
            ),
            patch(
                "apps.compiler_api.routes.workflows._get_or_create",
                return_value=record,
            ),
        ):
            payload = TransitionRequest(to="approved", actor="alice")

            with pytest.raises(HTTPException) as exc_info:
                await transition_workflow(
                    "svc-1",
                    1,
                    payload,
                    session=session,
                    caller=_caller(),
                )

        assert exc_info.value.status_code == 409
        assert "not allowed" in exc_info.value.detail

    async def test_transition_appends_history(self) -> None:
        record = _make_record(state="in_review", history=[])
        session = AsyncMock()
        session.refresh = AsyncMock()

        with (
            patch(
                "apps.compiler_api.routes.workflows._require_existing_service_version",
                return_value=None,
            ),
            patch(
                "apps.compiler_api.routes.workflows._get_or_create",
                return_value=record,
            ),
        ):
            payload = TransitionRequest(to="approved", actor="bob", comment="LGTM")
            result = await transition_workflow(
                "svc-1",
                1,
                payload,
                session=session,
                caller=_caller(
                    subject="reviewer@example.com",
                    username="reviewer",
                    roles=["reviewer"],
                ),
            )

        assert result.state == "approved"
        assert len(record.history) == 1
        assert record.history[0]["from"] == "in_review"
        assert record.history[0]["to"] == "approved"
        assert record.history[0]["actor"] == "reviewer"
        assert record.history[0]["comment"] == "LGTM"

    async def test_full_happy_path_transitions(self) -> None:
        """Walk through draft → submitted → in_review → approved → published → deployed."""
        transitions = [
            ("draft", "submitted"),
            ("submitted", "in_review"),
            ("in_review", "approved"),
            ("approved", "published"),
            ("published", "deployed"),
        ]
        for from_state, to_state in transitions:
            record = _make_record(state=from_state, history=[])
            session = AsyncMock()
            session.refresh = AsyncMock()

            with (
                patch(
                    "apps.compiler_api.routes.workflows._require_existing_service_version",
                    return_value=None,
                ),
                patch(
                    "apps.compiler_api.routes.workflows._get_or_create",
                    return_value=record,
                ),
            ):
                payload = TransitionRequest(to=to_state, actor="ci")
                result = await transition_workflow(
                    "svc-1",
                    1,
                    payload,
                    session=session,
                    caller=_caller(
                        subject="ci@example.com",
                        username="ci",
                        roles=["admin"],
                    ),
                )
            assert result.state == to_state, f"Expected {to_state} from {from_state}"

    async def test_rejects_missing_service_version(self) -> None:
        session = AsyncMock()
        payload = TransitionRequest(to="submitted", actor="alice")

        with (
            patch(
                "apps.compiler_api.routes.workflows._require_existing_service_version",
                side_effect=HTTPException(status_code=404, detail="missing"),
            ),
            patch(
                "apps.compiler_api.routes.workflows._get_or_create",
            ) as get_or_create,
        ):
            with pytest.raises(HTTPException) as exc_info:
                await transition_workflow(
                    "svc-1",
                    99,
                    payload,
                    session=session,
                    caller=_caller(),
                )

        assert exc_info.value.status_code == 404
        get_or_create.assert_not_called()

    async def test_uses_scope_when_transitioning(self) -> None:
        record = _make_record(state="draft", tenant="team-a", environment="prod")
        session = AsyncMock()
        session.refresh = AsyncMock()

        with (
            patch(
                "apps.compiler_api.routes.workflows._require_existing_service_version",
                return_value=None,
            ) as require_existing,
            patch(
                "apps.compiler_api.routes.workflows._get_or_create",
                return_value=record,
            ) as get_or_create,
        ):
            payload = TransitionRequest(to="submitted", actor="alice")
            result = await transition_workflow(
                "svc-1",
                1,
                payload,
                tenant="team-a",
                environment="prod",
                session=session,
                caller=_caller(subject="alice@example.com", username="alice"),
            )

        assert result.tenant == "team-a"
        assert result.environment == "prod"
        require_existing.assert_awaited_once_with(
            session,
            "svc-1",
            1,
            tenant="team-a",
            environment="prod",
        )
        get_or_create.assert_awaited_once_with(
            session,
            "svc-1",
            1,
            tenant="team-a",
            environment="prod",
        )


# ---------------------------------------------------------------------------
# Role-based transition gating
# ---------------------------------------------------------------------------


class TestTransitionRoleGating:
    """Verify that sensitive transitions enforce caller roles."""

    _GATED_TRANSITIONS = [
        ("in_review", "approved", "reviewer"),
        ("approved", "published", "publisher"),
        ("published", "deployed", "deployer"),
    ]

    async def _attempt_transition(
        self,
        from_state: str,
        to_state: str,
        *,
        roles: list[str] | None = None,
    ) -> None:
        record = _make_record(state=from_state, history=[])
        session = AsyncMock()
        session.refresh = AsyncMock()

        with (
            patch(
                "apps.compiler_api.routes.workflows._require_existing_service_version",
                return_value=None,
            ),
            patch(
                "apps.compiler_api.routes.workflows._get_or_create",
                return_value=record,
            ),
        ):
            payload = TransitionRequest(to=to_state, actor="tester")
            await transition_workflow(
                "svc-1",
                1,
                payload,
                session=session,
                caller=_caller(subject="tester", username="tester", roles=roles),
            )

    @pytest.mark.parametrize(
        "from_state,to_state,_required_role",
        _GATED_TRANSITIONS,
    )
    async def test_viewer_cannot_perform_gated_transition(
        self,
        from_state: str,
        to_state: str,
        _required_role: str,
    ) -> None:
        with pytest.raises(HTTPException) as exc_info:
            await self._attempt_transition(from_state, to_state, roles=["viewer"])
        assert exc_info.value.status_code == 403
        assert "requires one of" in exc_info.value.detail

    @pytest.mark.parametrize(
        "from_state,to_state,_required_role",
        _GATED_TRANSITIONS,
    )
    async def test_admin_can_perform_any_gated_transition(
        self,
        from_state: str,
        to_state: str,
        _required_role: str,
    ) -> None:
        await self._attempt_transition(from_state, to_state, roles=["admin"])

    @pytest.mark.parametrize(
        "from_state,to_state,required_role",
        _GATED_TRANSITIONS,
    )
    async def test_correct_role_can_perform_transition(
        self,
        from_state: str,
        to_state: str,
        required_role: str,
    ) -> None:
        await self._attempt_transition(from_state, to_state, roles=[required_role])

    async def test_ungated_transition_needs_no_special_role(self) -> None:
        """draft → submitted should succeed for any authenticated caller."""
        await self._attempt_transition("draft", "submitted", roles=["viewer"])

    async def test_no_roles_claim_is_rejected_for_gated_transition(self) -> None:
        with pytest.raises(HTTPException) as exc_info:
            await self._attempt_transition("in_review", "approved", roles=None)
        assert exc_info.value.status_code == 403


# ---------------------------------------------------------------------------
# PUT notes
# ---------------------------------------------------------------------------


class TestSaveReviewNotes:
    async def test_saves_notes(self) -> None:
        record = _make_record()
        session = AsyncMock()
        session.refresh = AsyncMock()

        with (
            patch(
                "apps.compiler_api.routes.workflows._require_existing_service_version",
                return_value=None,
            ),
            patch(
                "apps.compiler_api.routes.workflows._get_or_create",
                return_value=record,
            ),
        ):
            payload = ReviewNotesUpdate(
                notes={"op-1": "looks good", "op-2": "needs fix"},
                overall_note="Ship it",
                reviewed_operations=["op-1", "op-2"],
            )
            await save_review_notes(
                "svc-1",
                1,
                payload,
                session=session,
                _caller=_caller(),
            )

        assert record.review_notes == {
            "operation_notes": {"op-1": "looks good", "op-2": "needs fix"},
            "overall_note": "Ship it",
            "reviewed_operations": ["op-1", "op-2"],
        }
        session.commit.assert_awaited_once()

    async def test_saves_notes_without_overall(self) -> None:
        record = _make_record()
        session = AsyncMock()
        session.refresh = AsyncMock()

        with (
            patch(
                "apps.compiler_api.routes.workflows._require_existing_service_version",
                return_value=None,
            ),
            patch(
                "apps.compiler_api.routes.workflows._get_or_create",
                return_value=record,
            ),
        ):
            payload = ReviewNotesUpdate(notes={"op-1": "ok"})
            await save_review_notes("svc-1", 1, payload, session=session, _caller=_caller())

        assert record.review_notes["overall_note"] is None
        assert record.review_notes["reviewed_operations"] == []

    async def test_uses_scope_when_saving_notes(self) -> None:
        record = _make_record(tenant="team-a", environment="prod")
        session = AsyncMock()
        session.refresh = AsyncMock()

        with (
            patch(
                "apps.compiler_api.routes.workflows._require_existing_service_version",
                return_value=None,
            ) as require_existing,
            patch(
                "apps.compiler_api.routes.workflows._get_or_create",
                return_value=record,
            ) as get_or_create,
        ):
            payload = ReviewNotesUpdate(notes={"op-1": "ok"})
            result = await save_review_notes(
                "svc-1",
                1,
                payload,
                tenant="team-a",
                environment="prod",
                session=session,
                _caller=_caller(),
            )

        assert result.tenant == "team-a"
        assert result.environment == "prod"
        require_existing.assert_awaited_once_with(
            session,
            "svc-1",
            1,
            tenant="team-a",
            environment="prod",
        )
        get_or_create.assert_awaited_once_with(
            session,
            "svc-1",
            1,
            tenant="team-a",
            environment="prod",
        )


# ---------------------------------------------------------------------------
# GET history
# ---------------------------------------------------------------------------


class TestGetWorkflowHistory:
    async def test_returns_history_entries(self) -> None:
        history = [
            {
                "from": "draft",
                "to": "submitted",
                "actor": "alice",
                "comment": None,
                "timestamp": "2025-01-01T00:00:00+00:00",
            },
        ]
        record = _make_record(history=history)
        session = AsyncMock()

        with (
            patch(
                "apps.compiler_api.routes.workflows._require_existing_service_version",
                return_value=None,
            ),
            patch(
                "apps.compiler_api.routes.workflows._get_or_create",
                return_value=record,
            ),
        ):
            result = await get_workflow_history("svc-1", 1, session=session, _caller=_caller())

        assert len(result) == 1
        assert result[0].to == "submitted"

    async def test_empty_history(self) -> None:
        record = _make_record(history=[])
        session = AsyncMock()

        with (
            patch(
                "apps.compiler_api.routes.workflows._require_existing_service_version",
                return_value=None,
            ),
            patch(
                "apps.compiler_api.routes.workflows._get_or_create",
                return_value=record,
            ),
        ):
            result = await get_workflow_history("svc-1", 1, session=session, _caller=_caller())
        assert result == []

    async def test_uses_scope_when_loading_history(self) -> None:
        record = _make_record(history=[], tenant="team-a", environment="prod")
        session = AsyncMock()

        with (
            patch(
                "apps.compiler_api.routes.workflows._require_existing_service_version",
                return_value=None,
            ) as require_existing,
            patch(
                "apps.compiler_api.routes.workflows._get_or_create",
                return_value=record,
            ) as get_or_create,
        ):
            result = await get_workflow_history(
                "svc-1",
                1,
                tenant="team-a",
                environment="prod",
                session=session,
                _caller=_caller(),
            )

        assert result == []
        require_existing.assert_awaited_once_with(
            session,
            "svc-1",
            1,
            tenant="team-a",
            environment="prod",
        )
        get_or_create.assert_awaited_once_with(
            session,
            "svc-1",
            1,
            tenant="team-a",
            environment="prod",
        )
