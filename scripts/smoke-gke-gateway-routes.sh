#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
KUBECTL="${KUBECTL:-kubectl}"
NAMESPACE="${NAMESPACE:-tool-compiler-gateway-smoke}"
WAIT_TIMEOUT_SECONDS="${WAIT_TIMEOUT_SECONDS:-300}"
KEEP_NAMESPACE="${KEEP_NAMESPACE:-0}"
SKIP_MIGRATION="${SKIP_MIGRATION:-0}"
SERVICE_ID="${SERVICE_ID:-gateway-smoke-gke}"
SMOKE_MODE="${SMOKE_MODE:-reconcile}"
ACCESS_CONTROL_IMAGE="${ACCESS_CONTROL_IMAGE:-us-central1-docker.pkg.dev/insightcompass-465300/tool-compiler/access-control:20260325-h008-r13}"
COMPILER_API_IMAGE="${COMPILER_API_IMAGE:-us-central1-docker.pkg.dev/insightcompass-465300/tool-compiler/compiler-api:20260325-b0e27e6-r4}"
MIGRATION_IMAGE="${MIGRATION_IMAGE:-${COMPILER_API_IMAGE}}"
GATEWAY_ADMIN_IMAGE="${GATEWAY_ADMIN_IMAGE:-${ACCESS_CONTROL_IMAGE}}"

cleanup() {
  if [[ "${KEEP_NAMESPACE}" == "1" ]]; then
    echo "Keeping namespace ${NAMESPACE}"
    return
  fi
  "${KUBECTL}" delete namespace "${NAMESPACE}" --ignore-not-found >/dev/null 2>&1 || true
}

trap cleanup EXIT

"${KUBECTL}" get namespace "${NAMESPACE}" >/dev/null 2>&1 || "${KUBECTL}" create namespace "${NAMESPACE}" >/dev/null
"${KUBECTL}" delete job -n "${NAMESPACE}" gateway-smoke-migrate gateway-smoke-runner --ignore-not-found >/dev/null 2>&1 || true

"${KUBECTL}" apply -n "${NAMESPACE}" -f - <<YAML
apiVersion: v1
kind: Secret
metadata:
  name: gateway-smoke-secrets
type: Opaque
stringData:
  jwt-secret: gateway-smoke-jwt-secret
---
apiVersion: v1
kind: Service
metadata:
  name: gateway-smoke-postgres
spec:
  selector:
    app: gateway-smoke-postgres
  ports:
    - name: postgres
      port: 5432
      targetPort: 5432
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: gateway-smoke-postgres
spec:
  replicas: 1
  selector:
    matchLabels:
      app: gateway-smoke-postgres
  template:
    metadata:
      labels:
        app: gateway-smoke-postgres
    spec:
      containers:
        - name: postgres
          image: postgres:16-alpine
          env:
            - name: POSTGRES_DB
              value: toolcompiler
            - name: POSTGRES_USER
              value: toolcompiler
            - name: POSTGRES_PASSWORD
              value: toolcompiler
            - name: PGDATA
              value: /var/lib/postgresql/data/pgdata
          ports:
            - containerPort: 5432
          volumeMounts:
            - name: postgres-data
              mountPath: /var/lib/postgresql/data
      volumes:
        - name: postgres-data
          emptyDir: {}
---
apiVersion: v1
kind: Service
metadata:
  name: gateway-smoke-access-control
spec:
  selector:
    app: gateway-smoke-access-control
  ports:
    - name: http
      port: 8001
      targetPort: 8001
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: gateway-smoke-access-control
spec:
  replicas: 1
  selector:
    matchLabels:
      app: gateway-smoke-access-control
  template:
    metadata:
      labels:
        app: gateway-smoke-access-control
    spec:
      containers:
        - name: access-control
          image: ${ACCESS_CONTROL_IMAGE}
          command:
            - sh
            - -lc
            - python -m uvicorn apps.access_control.main:app --host 0.0.0.0 --port 8001
          env:
            - name: DATABASE_URL
              value: postgresql+asyncpg://toolcompiler:toolcompiler@gateway-smoke-postgres:5432/toolcompiler
            - name: ACCESS_CONTROL_JWT_SECRET
              valueFrom:
                secretKeyRef:
                  name: gateway-smoke-secrets
                  key: jwt-secret
            - name: GATEWAY_ADMIN_URL
              value: http://gateway-smoke-gateway-admin:8004
          ports:
            - name: http
              containerPort: 8001
---
apiVersion: v1
kind: Service
metadata:
  name: gateway-smoke-gateway-admin
spec:
  selector:
    app: gateway-smoke-gateway-admin
  ports:
    - name: http
      port: 8004
      targetPort: 8004
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: gateway-smoke-gateway-admin
spec:
  replicas: 1
  selector:
    matchLabels:
      app: gateway-smoke-gateway-admin
  template:
    metadata:
      labels:
        app: gateway-smoke-gateway-admin
    spec:
      containers:
        - name: gateway-admin
          image: ${GATEWAY_ADMIN_IMAGE}
          command:
            - sh
            - -lc
            - python -m uvicorn apps.gateway_admin_mock.main:app --host 0.0.0.0 --port 8004
          ports:
            - name: http
              containerPort: 8004
---
apiVersion: v1
kind: Service
metadata:
  name: gateway-smoke-runtime-v1
spec:
  selector:
    app: gateway-smoke-runtime-v1
  ports:
    - name: http
      port: 8003
      targetPort: 8003
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: gateway-smoke-runtime-v1
spec:
  replicas: 1
  selector:
    matchLabels:
      app: gateway-smoke-runtime-v1
  template:
    metadata:
      labels:
        app: gateway-smoke-runtime-v1
    spec:
      containers:
        - name: runtime-v1
          image: ${ACCESS_CONTROL_IMAGE}
          command:
            - sh
            - -lc
            - |
              python - <<'PY'
              import json
              from http.server import BaseHTTPRequestHandler, HTTPServer

              class Handler(BaseHTTPRequestHandler):
                  def do_GET(self):
                      body = json.dumps(
                          {
                              "service_name": "gateway-smoke-runtime-v1",
                              "version": "v1",
                              "path": self.path,
                          }
                      ).encode("utf-8")
                      self.send_response(200)
                      self.send_header("Content-Type", "application/json")
                      self.send_header("Content-Length", str(len(body)))
                      self.end_headers()
                      self.wfile.write(body)

                  def log_message(self, format, *args):
                      return

              HTTPServer(("0.0.0.0", 8003), Handler).serve_forever()
              PY
          ports:
            - name: http
              containerPort: 8003
---
apiVersion: v1
kind: Service
metadata:
  name: gateway-smoke-runtime-v2
spec:
  selector:
    app: gateway-smoke-runtime-v2
  ports:
    - name: http
      port: 8003
      targetPort: 8003
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: gateway-smoke-runtime-v2
spec:
  replicas: 1
  selector:
    matchLabels:
      app: gateway-smoke-runtime-v2
  template:
    metadata:
      labels:
        app: gateway-smoke-runtime-v2
    spec:
      containers:
        - name: runtime-v2
          image: ${ACCESS_CONTROL_IMAGE}
          command:
            - sh
            - -lc
            - |
              python - <<'PY'
              import json
              from http.server import BaseHTTPRequestHandler, HTTPServer

              class Handler(BaseHTTPRequestHandler):
                  def do_GET(self):
                      body = json.dumps(
                          {
                              "service_name": "gateway-smoke-runtime-v2",
                              "version": "v2",
                              "path": self.path,
                          }
                      ).encode("utf-8")
                      self.send_response(200)
                      self.send_header("Content-Type", "application/json")
                      self.send_header("Content-Length", str(len(body)))
                      self.end_headers()
                      self.wfile.write(body)

                  def log_message(self, format, *args):
                      return

              HTTPServer(("0.0.0.0", 8003), Handler).serve_forever()
              PY
          ports:
            - name: http
              containerPort: 8003
YAML

if [[ "${SKIP_MIGRATION}" != "1" ]]; then
"${KUBECTL}" apply -n "${NAMESPACE}" -f - <<YAML
apiVersion: batch/v1
kind: Job
metadata:
  name: gateway-smoke-migrate
spec:
  backoffLimit: 0
  template:
    spec:
      restartPolicy: Never
      containers:
        - name: migrate
          image: ${MIGRATION_IMAGE}
          command:
            - sh
            - -lc
            - |
              deadline=\$((\$(date +%s) + ${WAIT_TIMEOUT_SECONDS}))
              until python -c 'import psycopg; psycopg.connect("postgresql://toolcompiler:toolcompiler@gateway-smoke-postgres:5432/toolcompiler", connect_timeout=5).close()'
              do
                if [ "\$(date +%s)" -ge "\${deadline}" ]; then
                  echo "Timed out waiting for postgres"
                  exit 1
                fi
                sleep 2
              done
              alembic -c migrations/alembic.ini upgrade head
          env:
            - name: DATABASE_URL
              value: postgresql+psycopg://toolcompiler:toolcompiler@gateway-smoke-postgres:5432/toolcompiler
YAML

"${KUBECTL}" wait -n "${NAMESPACE}" --for=condition=complete job/gateway-smoke-migrate --timeout="${WAIT_TIMEOUT_SECONDS}s"
fi

"${KUBECTL}" apply -n "${NAMESPACE}" -f - <<YAML
apiVersion: batch/v1
kind: Job
metadata:
  name: gateway-smoke-runner
spec:
  backoffLimit: 0
  template:
    spec:
      restartPolicy: Never
      containers:
        - name: runner
          image: ${ACCESS_CONTROL_IMAGE}
          command:
            - python
            - -c
            - |
              from __future__ import annotations
              import json
              import time
              import uuid
              from pathlib import Path
              import httpx
              import psycopg
              from psycopg.types.json import Jsonb

              timeout_seconds = ${WAIT_TIMEOUT_SECONDS}
              service_id = "${SERVICE_ID}"
              smoke_mode = "${SMOKE_MODE}".strip().lower()
              runtime_namespace = "${NAMESPACE}"
              if smoke_mode not in {"reconcile", "rollout"}:
                  raise RuntimeError(f"Unsupported SMOKE_MODE {smoke_mode!r}.")
              route_service_v1 = "gateway-smoke-runtime-v1"
              route_service_v2 = "gateway-smoke-runtime-v2"
              ir_payload = json.loads(
                  Path("/app/tests/fixtures/ir/service_ir_valid.json").read_text(encoding="utf-8")
              )

              def build_route_config(version_number: int, service_name: str) -> dict[str, object]:
                  return {
                      "service_id": service_id,
                      "service_name": service_name,
                      "namespace": runtime_namespace,
                      "version_number": version_number,
                      "default_route": {
                          "route_id": f"{service_id}-active",
                          "target_service": {
                              "name": service_name,
                              "namespace": runtime_namespace,
                              "port": 8003,
                          },
                          "switch_strategy": "atomic-upstream-swap",
                      },
                      "version_route": {
                          "route_id": f"{service_id}-v{version_number}",
                          "match": {
                              "headers": {"x-tool-compiler-version": str(version_number)}
                          },
                          "target_service": {
                              "name": service_name,
                              "namespace": runtime_namespace,
                              "port": 8003,
                          },
                      },
                  }

              def write_version(
                  connection: psycopg.Connection,
                  *,
                  version_number: int,
                  service_name: str,
                  route_config: dict[str, object],
                  is_active: bool,
              ) -> None:
                  payload = dict(ir_payload)
                  payload["service_name"] = service_name
                  with connection.cursor() as cur:
                      cur.execute(
                          """
                          delete from registry.service_versions
                          where service_id = %s and version_number = %s
                          """,
                          (service_id, version_number),
                      )
                      cur.execute(
                          """
                          insert into registry.service_versions (
                              id,
                              service_id,
                              version_number,
                              is_active,
                              ir_json,
                              compiler_version,
                              protocol,
                              deployment_revision,
                              route_config
                          )
                          values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                          """,
                          (
                              uuid.uuid4(),
                              service_id,
                              version_number,
                              is_active,
                              Jsonb(payload),
                              "0.1.0",
                              payload.get("protocol"),
                              f"gateway-smoke-v{version_number}",
                              Jsonb(route_config),
                          ),
                      )

              def activate_version(connection: psycopg.Connection, version_number: int) -> None:
                  with connection.cursor() as cur:
                      cur.execute(
                          """
                          update registry.service_versions
                          set is_active = (version_number = %s)
                          where service_id = %s
                          """,
                          (version_number, service_id),
                      )

              route_config_v1 = build_route_config(1, route_service_v1)
              route_config_v2 = build_route_config(2, route_service_v2)

              deadline = time.monotonic() + timeout_seconds

              while True:
                  try:
                      psycopg.connect(
                          "postgresql://toolcompiler:toolcompiler@gateway-smoke-postgres:5432/toolcompiler",
                          connect_timeout=5,
                      ).close()
                      break
                  except Exception:
                      if time.monotonic() >= deadline:
                          raise
                      time.sleep(2)

              while True:
                  try:
                      with httpx.Client(timeout=5.0) as client:
                          access_health = client.get("http://gateway-smoke-access-control:8001/healthz")
                          gateway_health = client.get("http://gateway-smoke-gateway-admin:8004/healthz")
                          runtime_v1_health = client.get("http://gateway-smoke-runtime-v1:8003/healthz")
                          runtime_v2_health = client.get("http://gateway-smoke-runtime-v2:8003/healthz")
                      if (
                          access_health.status_code == 200
                          and gateway_health.status_code == 200
                          and runtime_v1_health.status_code == 200
                          and runtime_v2_health.status_code == 200
                      ):
                          break
                  except Exception:
                      pass
                  if time.monotonic() >= deadline:
                      raise RuntimeError(
                          "Timed out waiting for access-control, gateway-admin, and runtime health endpoints."
                      )
                  time.sleep(2)

              with psycopg.connect(
                  "postgresql://toolcompiler:toolcompiler@gateway-smoke-postgres:5432/toolcompiler",
                  connect_timeout=5,
              ) as conn:
                  with conn.cursor() as cur:
                      cur.execute(
                          "delete from registry.service_versions where service_id = %s",
                          (service_id,),
                      )
                  write_version(
                      conn,
                      version_number=1,
                      service_name=route_service_v1,
                      route_config=route_config_v1,
                      is_active=True,
                  )

              with httpx.Client(timeout=30.0) as client:
                  def gateway_call(version: str | None = None) -> httpx.Response:
                      headers = {}
                      if version is not None:
                          headers["x-tool-compiler-version"] = version
                      return client.get(
                          f"http://gateway-smoke-gateway-admin:8004/gateway/{service_id}/status",
                          headers=headers,
                          params={"source": smoke_mode},
                      )

                  sync_response = client.post(
                      "http://gateway-smoke-access-control:8001/api/v1/gateway-binding/service-routes/sync",
                      json={"route_config": route_config_v1},
                  )
                  sync_response.raise_for_status()

                  def list_routes() -> dict[str, dict[str, object]]:
                      routes_response = client.get("http://gateway-smoke-gateway-admin:8004/admin/routes")
                      routes_response.raise_for_status()
                      return {
                          item["route_id"]: item
                          for item in routes_response.json()["items"]
                      }

                  initial_routes = list_routes()
                  expected_v1 = {f"{service_id}-active", f"{service_id}-v1"}
                  if set(initial_routes) != expected_v1:
                      raise RuntimeError(f"Unexpected initial route set: {sorted(initial_routes)}")
                  initial_target = initial_routes[f"{service_id}-active"]["document"]["target_service"]["name"]
                  if initial_target != route_service_v1:
                      raise RuntimeError(f"Stable route target mismatch: {initial_target}")

                  initial_gateway = gateway_call()
                  initial_gateway.raise_for_status()
                  if initial_gateway.json()["service_name"] != route_service_v1:
                      raise RuntimeError(
                          f"Initial data-plane target mismatch: {initial_gateway.json()}"
                      )

                  if smoke_mode == "reconcile":
                      delete_response = client.delete(
                          f"http://gateway-smoke-gateway-admin:8004/admin/routes/{service_id}-active"
                      )
                      delete_response.raise_for_status()

                      missing_gateway = gateway_call()
                      if missing_gateway.status_code != 404:
                          raise RuntimeError(
                              f"Expected 404 from missing stable route, got {missing_gateway.status_code}: {missing_gateway.text}"
                          )

                      pinned_gateway = gateway_call("1")
                      pinned_gateway.raise_for_status()
                      if pinned_gateway.json()["service_name"] != route_service_v1:
                          raise RuntimeError(
                              f"Pinned route during drift mismatch: {pinned_gateway.json()}"
                          )

                      reconcile_response = client.post(
                          "http://gateway-smoke-access-control:8001/api/v1/gateway-binding/reconcile"
                      )
                      reconcile_response.raise_for_status()

                      restored_routes = list_routes()
                      if set(restored_routes) != expected_v1:
                          raise RuntimeError(
                              f"Unexpected restored route set: {sorted(restored_routes)}"
                          )
                      restored_target = restored_routes[f"{service_id}-active"]["document"]["target_service"]["name"]
                      if restored_target != route_service_v1:
                          raise RuntimeError(f"Restored route target mismatch: {restored_target}")

                      restored_gateway = gateway_call()
                      restored_gateway.raise_for_status()
                      if restored_gateway.json()["service_name"] != route_service_v1:
                          raise RuntimeError(
                              f"Restored data-plane target mismatch: {restored_gateway.json()}"
                          )

                      result = {
                          "mode": smoke_mode,
                          "status": "ok",
                          "service_id": service_id,
                          "route_ids": sorted(restored_routes),
                          "stable_target": restored_target,
                          "gateway_missing_status": missing_gateway.status_code,
                          "gateway_active": restored_gateway.json(),
                          "gateway_pinned": pinned_gateway.json(),
                          "sync": sync_response.json(),
                          "reconcile": reconcile_response.json(),
                      }
                      print(json.dumps(result, indent=2, sort_keys=True))
                  else:
                      with psycopg.connect(
                          "postgresql://toolcompiler:toolcompiler@gateway-smoke-postgres:5432/toolcompiler",
                          connect_timeout=5,
                      ) as conn:
                          activate_version(conn, 0)
                          write_version(
                              conn,
                              version_number=2,
                              service_name=route_service_v2,
                              route_config=route_config_v2,
                              is_active=True,
                          )

                      rollout_response = client.post(
                          "http://gateway-smoke-access-control:8001/api/v1/gateway-binding/reconcile"
                      )
                      rollout_response.raise_for_status()

                      rolled_forward_routes = list_routes()
                      expected_rollout = {
                          f"{service_id}-active",
                          f"{service_id}-v1",
                          f"{service_id}-v2",
                      }
                      if set(rolled_forward_routes) != expected_rollout:
                          raise RuntimeError(
                              f"Unexpected rollout route set: {sorted(rolled_forward_routes)}"
                          )
                      forward_target = rolled_forward_routes[f"{service_id}-active"]["document"]["target_service"]["name"]
                      if forward_target != route_service_v2:
                          raise RuntimeError(f"Rollout target mismatch: {forward_target}")

                      gateway_after_forward = gateway_call()
                      gateway_after_forward.raise_for_status()
                      if gateway_after_forward.json()["service_name"] != route_service_v2:
                          raise RuntimeError(
                              f"Forward data-plane target mismatch: {gateway_after_forward.json()}"
                          )

                      gateway_pinned_v1 = gateway_call("1")
                      gateway_pinned_v1.raise_for_status()
                      if gateway_pinned_v1.json()["service_name"] != route_service_v1:
                          raise RuntimeError(
                              f"Pinned v1 target mismatch after rollout: {gateway_pinned_v1.json()}"
                          )

                      gateway_pinned_v2 = gateway_call("2")
                      gateway_pinned_v2.raise_for_status()
                      if gateway_pinned_v2.json()["service_name"] != route_service_v2:
                          raise RuntimeError(
                              f"Pinned v2 target mismatch after rollout: {gateway_pinned_v2.json()}"
                          )

                      with psycopg.connect(
                          "postgresql://toolcompiler:toolcompiler@gateway-smoke-postgres:5432/toolcompiler",
                          connect_timeout=5,
                      ) as conn:
                          activate_version(conn, 1)

                      rollback_response = client.post(
                          "http://gateway-smoke-access-control:8001/api/v1/gateway-binding/reconcile"
                      )
                      rollback_response.raise_for_status()

                      rolled_back_routes = list_routes()
                      if set(rolled_back_routes) != expected_rollout:
                          raise RuntimeError(
                              f"Unexpected rollback route set: {sorted(rolled_back_routes)}"
                          )
                      rollback_target = rolled_back_routes[f"{service_id}-active"]["document"]["target_service"]["name"]
                      if rollback_target != route_service_v1:
                          raise RuntimeError(f"Rollback target mismatch: {rollback_target}")

                      gateway_after_rollback = gateway_call()
                      gateway_after_rollback.raise_for_status()
                      if gateway_after_rollback.json()["service_name"] != route_service_v1:
                          raise RuntimeError(
                              f"Rollback data-plane target mismatch: {gateway_after_rollback.json()}"
                          )

                      gateway_pinned_v2_after_rollback = gateway_call("2")
                      gateway_pinned_v2_after_rollback.raise_for_status()
                      if (
                          gateway_pinned_v2_after_rollback.json()["service_name"]
                          != route_service_v2
                      ):
                          raise RuntimeError(
                              "Pinned v2 target mismatch after rollback: "
                              f"{gateway_pinned_v2_after_rollback.json()}"
                          )

                      result = {
                          "mode": smoke_mode,
                          "status": "ok",
                          "service_id": service_id,
                          "route_ids": sorted(rolled_back_routes),
                          "stable_target": rollback_target,
                          "gateway_active_after_forward": gateway_after_forward.json(),
                          "gateway_pinned_v1": gateway_pinned_v1.json(),
                          "gateway_pinned_v2": gateway_pinned_v2.json(),
                          "gateway_active_after_rollback": gateway_after_rollback.json(),
                          "gateway_pinned_v2_after_rollback": gateway_pinned_v2_after_rollback.json(),
                          "sync": sync_response.json(),
                          "rollout": rollout_response.json(),
                          "rollback": rollback_response.json(),
                      }
                      print(json.dumps(result, indent=2, sort_keys=True))
YAML

"${KUBECTL}" wait -n "${NAMESPACE}" --for=condition=complete job/gateway-smoke-runner --timeout="${WAIT_TIMEOUT_SECONDS}s"
"${KUBECTL}" logs -n "${NAMESPACE}" job/gateway-smoke-runner
