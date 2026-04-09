#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

export COMPILER_API_URL="${COMPILER_API_URL:-http://127.0.0.1:8000}"
export ACCESS_CONTROL_URL="${ACCESS_CONTROL_URL:-http://127.0.0.1:8001}"
export GATEWAY_ADMIN_URL="${GATEWAY_ADMIN_URL:-http://127.0.0.1:8004}"
export SOURCE_FILE="${SOURCE_FILE:-${ROOT_DIR}/tests/fixtures/openapi_specs/petstore_3_0.yaml}"
export IR_FILE="${IR_FILE:-${ROOT_DIR}/tests/fixtures/ir/service_ir_valid.json}"
export SERVICE_ID="${SERVICE_ID:-gateway-smoke-api}"
export CREATED_BY="${CREATED_BY:-gateway-smoke}"
export SMOKE_MODE="${SMOKE_MODE:-artifact}"
export JOB_TIMEOUT_SECONDS="${JOB_TIMEOUT_SECONDS:-240}"
export POLL_INTERVAL_SECONDS="${POLL_INTERVAL_SECONDS:-2}"

python3 - <<'PY'
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


def request_json(
    method: str,
    url: str,
    *,
    payload: dict[str, Any] | None = None,
    expected_status: int = 200,
    timeout: float = 30.0,
) -> dict[str, Any]:
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
            if response.status != expected_status:
                raise RuntimeError(f"{method} {url} returned {response.status}: {body}")
            if not body:
                return {}
            parsed = json.loads(body)
            if not isinstance(parsed, dict):
                raise RuntimeError(f"{method} {url} returned a non-object payload.")
            return parsed
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {url} failed with {exc.code}: {detail}") from exc


def require_health(base_url: str) -> None:
    payload = request_json("GET", f"{base_url.rstrip('/')}/healthz")
    if payload.get("status") != "ok":
        raise RuntimeError(f"Health check failed for {base_url}: {payload}")


def find_service(services_payload: dict[str, Any], service_id: str) -> dict[str, Any]:
    services = services_payload.get("services")
    if not isinstance(services, list):
        raise RuntimeError("Service list response did not include a services array.")
    for item in services:
        if isinstance(item, dict) and item.get("service_id") == service_id:
            return item
    raise RuntimeError(f"Compiled service {service_id!r} was not found in /api/v1/services.")


def build_route_config(
    *,
    service_id: str,
    service_name: str,
    namespace: str,
    version_number: int,
) -> dict[str, Any]:
    target_service = {
        "name": service_name,
        "namespace": namespace,
        "port": 8003,
    }
    return {
        "service_id": service_id,
        "service_name": service_name,
        "namespace": namespace,
        "version_number": version_number,
        "default_route": {
            "route_id": f"{service_id}-active",
            "target_service": target_service,
            "switch_strategy": "atomic-upstream-swap",
        },
        "version_route": {
            "route_id": f"{service_id}-v{version_number}",
            "match": {"headers": {"x-tool-compiler-version": str(version_number)}},
            "target_service": target_service,
        },
    }


compiler_api_url = os.environ["COMPILER_API_URL"].rstrip("/")
access_control_url = os.environ["ACCESS_CONTROL_URL"].rstrip("/")
gateway_admin_url = os.environ["GATEWAY_ADMIN_URL"].rstrip("/")
source_file = Path(os.environ["SOURCE_FILE"])
ir_file = Path(os.environ["IR_FILE"])
service_id = os.environ["SERVICE_ID"]
created_by = os.environ["CREATED_BY"]
smoke_mode = os.environ["SMOKE_MODE"].strip().lower()
job_timeout_seconds = int(os.environ["JOB_TIMEOUT_SECONDS"])
poll_interval_seconds = float(os.environ["POLL_INTERVAL_SECONDS"])

require_health(compiler_api_url)
require_health(access_control_url)
require_health(gateway_admin_url)
if smoke_mode not in {"artifact", "compile"}:
    raise RuntimeError(f"Unsupported SMOKE_MODE {smoke_mode!r}. Expected artifact or compile.")

job_id: str | None = None
active_version: int

if smoke_mode == "compile":
    if not source_file.exists():
        raise RuntimeError(f"Source file does not exist: {source_file}")

    submission = request_json(
        "POST",
        f"{compiler_api_url}/api/v1/compilations",
        payload={
            "source_url": "https://example.com/petstore.yaml",
            "source_content": source_file.read_text(encoding="utf-8"),
            "created_by": created_by,
            "service_name": service_id,
        },
        expected_status=202,
        timeout=60.0,
    )
    job_id = submission.get("job_id")
    if not isinstance(job_id, str) or not job_id:
        raise RuntimeError(f"Compilation submission did not return a job_id: {submission}")

    deadline = time.time() + job_timeout_seconds
    while time.time() < deadline:
        job_payload = request_json("GET", f"{compiler_api_url}/api/v1/compilations/{job_id}")
        status = job_payload.get("status")
        if status == "succeeded":
            break
        if status in {"failed", "rolled_back"}:
            raise RuntimeError(f"Compilation job {job_id} ended in {status}: {job_payload}")
        time.sleep(poll_interval_seconds)
    else:
        raise RuntimeError(f"Timed out waiting for compilation job {job_id} to succeed.")

    services_payload = request_json("GET", f"{compiler_api_url}/api/v1/services")
    service = find_service(services_payload, service_id)
    active_version = service.get("active_version")
    if not isinstance(active_version, int):
        raise RuntimeError(f"Service {service_id!r} did not expose an integer active_version.")
    route_config = None
else:
    if not ir_file.exists():
        raise RuntimeError(f"IR file does not exist: {ir_file}")

    versions_payload = request_json("GET", f"{compiler_api_url}/api/v1/artifacts/{service_id}/versions")
    versions = versions_payload.get("versions")
    if not isinstance(versions, list):
        raise RuntimeError("Artifact version list response did not include a versions array.")
    current_max = 0
    for version in versions:
        if not isinstance(version, dict):
            continue
        version_number = version.get("version_number")
        if isinstance(version_number, int):
            current_max = max(current_max, version_number)
    active_version = current_max + 1
    service_name = f"{service_id}-v{active_version}"
    route_config = build_route_config(
        service_id=service_id,
        service_name=service_name,
        namespace="runtime-system",
        version_number=active_version,
    )
    ir_payload = json.loads(ir_file.read_text(encoding="utf-8"))
    if not isinstance(ir_payload, dict):
        raise RuntimeError("IR fixture must decode to an object.")
    ir_payload["service_name"] = service_name
    artifact_payload = {
        "service_id": service_id,
        "version_number": active_version,
        "ir_json": ir_payload,
        "compiler_version": "0.1.0",
        "protocol": ir_payload.get("protocol"),
        "deployment_revision": f"gateway-smoke-v{active_version}",
        "route_config": route_config,
        "is_active": True,
    }
    request_json(
        "POST",
        f"{compiler_api_url}/api/v1/artifacts",
        payload=artifact_payload,
        expected_status=201,
        timeout=60.0,
    )
    request_json(
        "POST",
        f"{access_control_url}/api/v1/gateway-binding/service-routes/sync",
        payload={"route_config": route_config},
        expected_status=200,
    )

default_route_id = f"{service_id}-active"
version_route_id = f"{service_id}-v{active_version}"
routes_payload = request_json("GET", f"{gateway_admin_url}/admin/routes")
items = routes_payload.get("items")
if not isinstance(items, list):
    raise RuntimeError("Gateway admin route list did not include an items array.")
route_ids = {
    item.get("route_id")
    for item in items
    if isinstance(item, dict)
}
if default_route_id not in route_ids or version_route_id not in route_ids:
    raise RuntimeError(
        f"Expected routes {default_route_id!r} and {version_route_id!r}, got {sorted(route_ids)}"
    )

request_json(
    "DELETE",
    f"{gateway_admin_url}/admin/routes/{default_route_id}",
    expected_status=200,
)
request_json("POST", f"{access_control_url}/api/v1/gateway-binding/reconcile", expected_status=200)

restored_payload = request_json("GET", f"{gateway_admin_url}/admin/routes")
restored_items = restored_payload.get("items")
if not isinstance(restored_items, list):
    raise RuntimeError("Gateway admin route list after reconcile did not include an items array.")
restored_ids = {
    item.get("route_id")
    for item in restored_items
    if isinstance(item, dict)
}
if default_route_id not in restored_ids:
    raise RuntimeError(
        f"Route {default_route_id!r} was not restored after reconcile: {sorted(restored_ids)}"
    )

result = {
    "mode": smoke_mode,
    "service_id": service_id,
    "active_version": active_version,
    "route_ids": sorted(str(route_id) for route_id in restored_ids if route_id),
    "status": "ok",
}
if job_id is not None:
    result["job_id"] = job_id
if route_config is not None:
    result["route_config"] = route_config

print(json.dumps(result, indent=2, sort_keys=True))
PY
