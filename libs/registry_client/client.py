"""Async client for the artifact registry API."""

from __future__ import annotations

from typing import Self, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from libs.registry_client.models import (
    ArtifactDiffResponse,
    ArtifactVersionCreate,
    ArtifactVersionListResponse,
    ArtifactVersionResponse,
    ArtifactVersionUpdate,
)


class RegistryClientError(RuntimeError):
    """Raised when the registry API returns an unsuccessful response."""


ModelT = TypeVar("ModelT", bound=BaseModel)


class RegistryClient:
    """Thin async client for the registry endpoints."""

    def __init__(
        self,
        base_url: str,
        *,
        client: httpx.AsyncClient | None = None,
        timeout: float = 10.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = client or httpx.AsyncClient(base_url=self._base_url, timeout=timeout)
        self._owns_client = client is None

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def create_version(self, payload: ArtifactVersionCreate) -> ArtifactVersionResponse:
        response = await self._client.post(
            "/api/v1/artifacts",
            json=payload.model_dump(mode="json"),
        )
        return self._parse_model(response, ArtifactVersionResponse)

    async def list_versions(
        self,
        service_id: str,
        *,
        tenant: str | None = None,
        environment: str | None = None,
    ) -> ArtifactVersionListResponse:
        response = await self._client.get(
            f"/api/v1/artifacts/{service_id}/versions",
            params=self._filter_params(tenant=tenant, environment=environment),
        )
        return self._parse_model(response, ArtifactVersionListResponse)

    async def get_version(
        self,
        service_id: str,
        version_number: int,
        *,
        tenant: str | None = None,
        environment: str | None = None,
    ) -> ArtifactVersionResponse:
        response = await self._client.get(
            f"/api/v1/artifacts/{service_id}/versions/{version_number}",
            params=self._filter_params(tenant=tenant, environment=environment),
        )
        return self._parse_model(response, ArtifactVersionResponse)

    async def update_version(
        self,
        service_id: str,
        version_number: int,
        payload: ArtifactVersionUpdate,
        *,
        tenant: str | None = None,
        environment: str | None = None,
    ) -> ArtifactVersionResponse:
        response = await self._client.put(
            f"/api/v1/artifacts/{service_id}/versions/{version_number}",
            json=payload.model_dump(mode="json", exclude_none=True),
            params=self._filter_params(tenant=tenant, environment=environment),
        )
        return self._parse_model(response, ArtifactVersionResponse)

    async def activate_version(
        self,
        service_id: str,
        version_number: int,
        *,
        tenant: str | None = None,
        environment: str | None = None,
    ) -> ArtifactVersionResponse:
        response = await self._client.post(
            f"/api/v1/artifacts/{service_id}/versions/{version_number}/activate",
            params=self._filter_params(tenant=tenant, environment=environment),
        )
        return self._parse_model(response, ArtifactVersionResponse)

    async def delete_version(
        self,
        service_id: str,
        version_number: int,
        *,
        tenant: str | None = None,
        environment: str | None = None,
    ) -> None:
        response = await self._client.delete(
            f"/api/v1/artifacts/{service_id}/versions/{version_number}",
            params=self._filter_params(tenant=tenant, environment=environment),
        )
        self._ensure_success(response)

    async def diff_versions(
        self,
        service_id: str,
        *,
        from_version: int,
        to_version: int,
        tenant: str | None = None,
        environment: str | None = None,
    ) -> ArtifactDiffResponse:
        params = self._filter_params(tenant=tenant, environment=environment)
        params["from"] = from_version
        params["to"] = to_version
        response = await self._client.get(f"/api/v1/artifacts/{service_id}/diff", params=params)
        return self._parse_model(response, ArtifactDiffResponse)

    @staticmethod
    def _filter_params(
        *,
        tenant: str | None = None,
        environment: str | None = None,
    ) -> dict[str, str | int]:
        params: dict[str, str | int] = {}
        if tenant is not None:
            params["tenant"] = tenant
        if environment is not None:
            params["environment"] = environment
        return params

    @staticmethod
    def _ensure_success(response: httpx.Response) -> None:
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise RegistryClientError(str(exc)) from exc

    @classmethod
    def _parse_model(cls, response: httpx.Response, model_type: type[ModelT]) -> ModelT:
        cls._ensure_success(response)
        try:
            data = response.json()
        except (ValueError, UnicodeDecodeError) as exc:
            snippet = response.text[:200] if response.text else "<empty>"
            raise RegistryClientError(
                f"Non-JSON response from registry: {response.status_code} — {snippet}"
            ) from exc
        try:
            return model_type.model_validate(data)
        except ValidationError as exc:
            raise RegistryClientError(
                f"Malformed JSON response from registry: {response.status_code}"
            ) from exc
