"""Authorization policy CRUD and evaluation service."""

from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatchcase
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.access_control.authz.models import (
    PolicyCreateRequest,
    PolicyEvaluationRequest,
    PolicyEvaluationResponse,
    PolicyResponse,
    PolicyUpdateRequest,
)
from libs.db_models import Policy
from libs.ir.models import RiskLevel

_RISK_ORDER = {
    RiskLevel.safe: 0,
    RiskLevel.cautious: 1,
    RiskLevel.dangerous: 2,
    RiskLevel.unknown: 3,
}
_DECISION_PRIORITY = {"deny": 3, "require_approval": 2, "allow": 1}


@dataclass(frozen=True)
class _MatchedPolicy:
    policy: Policy
    specificity: int


class AuthzService:
    """Policy CRUD and semantic evaluation helpers."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_policy(self, payload: PolicyCreateRequest) -> PolicyResponse:
        policy = Policy(
            subject_type=payload.subject_type,
            subject_id=payload.subject_id,
            resource_id=payload.resource_id,
            action_pattern=payload.action_pattern,
            risk_threshold=payload.risk_threshold.value,
            decision=payload.decision,
            created_by=payload.created_by,
        )
        self._session.add(policy)
        await self._session.commit()
        await self._session.refresh(policy)
        return self._to_response(policy)

    async def list_policies(
        self,
        *,
        subject_type: str | None = None,
        subject_id: str | None = None,
        resource_id: str | None = None,
    ) -> list[PolicyResponse]:
        query = select(Policy)
        if subject_type is not None:
            query = query.where(Policy.subject_type == subject_type)
        if subject_id is not None:
            query = query.where(Policy.subject_id == subject_id)
        if resource_id is not None:
            query = query.where(Policy.resource_id == resource_id)

        result = await self._session.scalars(
            query.order_by(Policy.created_at.desc()).limit(1000)
        )
        return [self._to_response(policy) for policy in result.all()]

    async def get_policy(self, policy_id: UUID) -> PolicyResponse | None:
        policy = await self._session.get(Policy, policy_id)
        if policy is None:
            return None
        return self._to_response(policy)

    async def update_policy(
        self,
        policy_id: UUID,
        payload: PolicyUpdateRequest,
    ) -> PolicyResponse | None:
        policy = await self._session.get(Policy, policy_id)
        if policy is None:
            return None

        if payload.resource_id is not None:
            policy.resource_id = payload.resource_id
        if payload.action_pattern is not None:
            policy.action_pattern = payload.action_pattern
        if payload.risk_threshold is not None:
            policy.risk_threshold = payload.risk_threshold.value
        if payload.decision is not None:
            policy.decision = payload.decision
        if payload.created_by is not None:
            policy.created_by = payload.created_by

        await self._session.commit()
        await self._session.refresh(policy)
        return self._to_response(policy)

    async def delete_policy(self, policy_id: UUID) -> bool:
        policy = await self._session.get(Policy, policy_id)
        if policy is None:
            return False

        await self._session.delete(policy)
        await self._session.commit()
        return True

    async def evaluate(self, payload: PolicyEvaluationRequest) -> PolicyEvaluationResponse:
        result = await self._session.scalars(
            select(Policy)
            .where(Policy.subject_type == payload.subject_type)
            .order_by(Policy.id)
        )
        candidates = [
            policy
            for policy in result.all()
            if policy.subject_id in {payload.subject_id, "*"}
        ]

        matches = [
            _MatchedPolicy(policy=policy, specificity=self._specificity(policy, payload))
            for policy in candidates
            if self._matches(policy, payload)
        ]
        if not matches:
            return PolicyEvaluationResponse(
                decision="deny",
                matched_policy_id=None,
                reason="No matching policy. Default deny applied.",
            )

        matches.sort(
            key=lambda match: (
                match.specificity,
                _DECISION_PRIORITY.get(match.policy.decision, 0),
            ),
            reverse=True,
        )
        highest_specificity = matches[0].specificity
        top_matches = [match for match in matches if match.specificity == highest_specificity]

        chosen = top_matches[0]
        return PolicyEvaluationResponse(
            decision=chosen.policy.decision,
            matched_policy_id=chosen.policy.id,
            reason=(
                f"Matched policy for subject={payload.subject_id}, "
                f"resource={payload.resource_id}, action={payload.action}."
            ),
        )

    def _matches(self, policy: Policy, payload: PolicyEvaluationRequest) -> bool:
        if policy.resource_id not in {"*", payload.resource_id}:
            return False
        if not fnmatchcase(payload.action, policy.action_pattern):
            return False

        try:
            threshold = RiskLevel(policy.risk_threshold)
        except ValueError:
            return False
        return _RISK_ORDER.get(payload.risk_level, 999) <= _RISK_ORDER.get(threshold, 999)

    def _specificity(self, policy: Policy, payload: PolicyEvaluationRequest) -> int:
        score = 0
        if policy.subject_id == payload.subject_id:
            score += 4
        if policy.resource_id == payload.resource_id:
            score += 2
        if policy.action_pattern == payload.action:
            score += 1
        return score

    @staticmethod
    def _to_response(policy: Policy) -> PolicyResponse:
        return PolicyResponse(
            id=policy.id,
            subject_type=policy.subject_type,
            subject_id=policy.subject_id,
            resource_id=policy.resource_id,
            action_pattern=policy.action_pattern,
            risk_threshold=RiskLevel(policy.risk_threshold),
            decision=policy.decision,
            created_by=policy.created_by,
            created_at=policy.created_at,
        )
