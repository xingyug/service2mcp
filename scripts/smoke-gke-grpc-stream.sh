#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
KUBECTL="${KUBECTL:-kubectl}"
DEFAULT_PYTHON_BIN="${ROOT_DIR}/.venv/bin/python"
if [[ -x "${DEFAULT_PYTHON_BIN}" ]]; then
  PYTHON_BIN="${PYTHON_BIN:-${DEFAULT_PYTHON_BIN}}"
else
  PYTHON_BIN="${PYTHON_BIN:-python3}"
fi
NAMESPACE="${NAMESPACE:-tool-compiler-grpc-stream-smoke}"
WAIT_TIMEOUT_SECONDS="${WAIT_TIMEOUT_SECONDS:-300}"
KEEP_NAMESPACE="${KEEP_NAMESPACE:-0}"
SERVICE_ID="${SERVICE_ID:-grpc-stream-live}"
RUNTIME_IMAGE="${RUNTIME_IMAGE:-us-central1-docker.pkg.dev/insightcompass-465300/tool-compiler/mcp-runtime:20260325-b0e27e6-r19}"
UPSTREAM_IMAGE="${UPSTREAM_IMAGE:-${RUNTIME_IMAGE}}"
IMAGE_PULL_POLICY="${IMAGE_PULL_POLICY:-Always}"
TMP_DIR="$(mktemp -d)"
RUNTIME_MANIFEST_PATH="${TMP_DIR}/runtime.yaml"
RUNTIME_METADATA_PATH="${TMP_DIR}/runtime-metadata.json"

cleanup() {
  rm -rf "${TMP_DIR}"
  if [[ "${KEEP_NAMESPACE}" == "1" ]]; then
    echo "Keeping namespace ${NAMESPACE}"
    return
  fi
  "${KUBECTL}" delete namespace "${NAMESPACE}" --ignore-not-found >/dev/null 2>&1 || true
}

trap cleanup EXIT

cd "${ROOT_DIR}"

export IMAGE_PULL_POLICY
export NAMESPACE
export RUNTIME_IMAGE
export RUNTIME_MANIFEST_PATH
export RUNTIME_METADATA_PATH
export SERVICE_ID

"${PYTHON_BIN}" - <<'PY'
import json
import os
from pathlib import Path

from libs.generator.generic_mode import GenericManifestConfig, generate_generic_manifests
from libs.ir.models import (
    AuthConfig,
    AuthType,
    EventDescriptor,
    EventSupportLevel,
    EventTransport,
    GrpcStreamMode,
    GrpcStreamRuntimeConfig,
    Operation,
    Param,
    RiskLevel,
    RiskMetadata,
    ServiceIR,
    SourceType,
)

namespace = os.environ["NAMESPACE"]
service_id = os.environ["SERVICE_ID"]
runtime_image = os.environ["RUNTIME_IMAGE"]
image_pull_policy = os.environ["IMAGE_PULL_POLICY"]

service_ir = ServiceIR(
    source_hash="1" * 64,
    protocol="grpc",
    service_name=service_id,
    service_description="Live GKE native grpc_stream smoke runtime",
    base_url=f"grpc://grpc-stream-upstream.{namespace}.svc.cluster.local:50051",
    auth=AuthConfig(type=AuthType.none),
    operations=[
        Operation(
            id="watchInventory",
            name="Watch Inventory",
            description="Consume a native gRPC inventory stream.",
            method="POST",
            path="/catalog.v1.InventoryService/WatchInventory",
            params=[Param(name="payload", type="object", required=False)],
            risk=RiskMetadata(
                risk_level=RiskLevel.safe,
                confidence=1.0,
                source=SourceType.extractor,
                writes_state=False,
                destructive=False,
                external_side_effect=False,
                idempotent=True,
            ),
            enabled=True,
        )
    ],
    event_descriptors=[
        EventDescriptor(
            id="WatchInventory",
            name="WatchInventory",
            description="Live native gRPC server-stream smoke descriptor.",
            transport=EventTransport.grpc_stream,
            support=EventSupportLevel.supported,
            operation_id="watchInventory",
            channel="/catalog.v1.InventoryService/WatchInventory",
            grpc_stream=GrpcStreamRuntimeConfig(
                rpc_path="/catalog.v1.InventoryService/WatchInventory",
                mode=GrpcStreamMode.server,
                max_messages=1,
                idle_timeout_seconds=5.0,
            ),
        )
    ],
    metadata={"smoke": "gke-native-grpc-stream"},
    environment="gke-smoke",
)

manifests = generate_generic_manifests(
    service_ir,
    config=GenericManifestConfig(
        runtime_image=runtime_image,
        service_id=service_id,
        version_number=1,
        namespace=namespace,
        image_pull_policy=image_pull_policy,
    ),
)

Path(os.environ["RUNTIME_MANIFEST_PATH"]).write_text(manifests.yaml, encoding="utf-8")
Path(os.environ["RUNTIME_METADATA_PATH"]).write_text(
    json.dumps(
        {
            "deployment_name": manifests.deployment["metadata"]["name"],
            "service_name": manifests.service["metadata"]["name"],
            "config_map_name": manifests.config_map["metadata"]["name"],
        },
        indent=2,
        sort_keys=True,
    ),
    encoding="utf-8",
)
PY

if ! grep -q "ENABLE_NATIVE_GRPC_STREAM" "${RUNTIME_MANIFEST_PATH}"; then
  echo "Generated runtime manifest did not enable native grpc stream support." >&2
  exit 1
fi

RUNTIME_DEPLOYMENT_NAME="$("${PYTHON_BIN}" -c 'import json,sys; print(json.load(open(sys.argv[1]))["deployment_name"])' "${RUNTIME_METADATA_PATH}")"
RUNTIME_SERVICE_NAME="$("${PYTHON_BIN}" -c 'import json,sys; print(json.load(open(sys.argv[1]))["service_name"])' "${RUNTIME_METADATA_PATH}")"
RUNTIME_CONFIGMAP_NAME="$("${PYTHON_BIN}" -c 'import json,sys; print(json.load(open(sys.argv[1]))["config_map_name"])' "${RUNTIME_METADATA_PATH}")"

wait_for_single_ready_pod() {
  local selector="$1"
  local description="$2"
  local elapsed=0

  while (( elapsed <= WAIT_TIMEOUT_SECONDS )); do
    local ready_count
    ready_count="$("${KUBECTL}" get pods -n "${NAMESPACE}" -l "${selector}" -o json | "${PYTHON_BIN}" -c 'import json,sys
pods=json.load(sys.stdin).get("items", [])
ready=0
for pod in pods:
    metadata = pod.get("metadata", {})
    if metadata.get("deletionTimestamp"):
        continue
    status = pod.get("status", {})
    if status.get("phase") != "Running":
        continue
    conditions = {
        item.get("type"): item.get("status")
        for item in status.get("conditions", [])
        if isinstance(item, dict)
    }
    if conditions.get("Ready") == "True":
        ready += 1
print(ready)')"
    if [[ "${ready_count}" == "1" ]]; then
      return 0
    fi
    sleep 2
    elapsed=$((elapsed + 2))
  done

  echo "Timed out waiting for a single ready pod for ${description}." >&2
  "${KUBECTL}" get pods -n "${NAMESPACE}" -l "${selector}" -o wide >&2 || true
  return 1
}

"${KUBECTL}" get namespace "${NAMESPACE}" >/dev/null 2>&1 || "${KUBECTL}" create namespace "${NAMESPACE}" >/dev/null
"${KUBECTL}" delete job -n "${NAMESPACE}" grpc-stream-smoke-runner --ignore-not-found >/dev/null 2>&1 || true

"${KUBECTL}" apply -f "${RUNTIME_MANIFEST_PATH}"

"${KUBECTL}" apply -n "${NAMESPACE}" -f - <<YAML
apiVersion: v1
kind: Service
metadata:
  name: grpc-stream-upstream
spec:
  selector:
    app: grpc-stream-upstream
  ports:
    - name: grpc
      port: 50051
      targetPort: 50051
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: grpc-stream-upstream
spec:
  replicas: 1
  selector:
    matchLabels:
      app: grpc-stream-upstream
  template:
    metadata:
      labels:
        app: grpc-stream-upstream
    spec:
      containers:
        - name: grpc-stream-upstream
          image: ${UPSTREAM_IMAGE}
          imagePullPolicy: ${IMAGE_PULL_POLICY}
          command:
            - sh
            - -lc
            - |
              python - <<'PY'
              from concurrent import futures

              import grpc
              from google.protobuf import descriptor_pb2
              from google.protobuf.descriptor_database import DescriptorDatabase
              from google.protobuf.descriptor_pool import DescriptorPool
              from google.protobuf.message_factory import GetMessageClass
              from grpc_reflection.v1alpha import reflection

              file_proto = descriptor_pb2.FileDescriptorProto()
              file_proto.name = "inventory_stream.proto"
              file_proto.package = "catalog.v1"
              file_proto.syntax = "proto3"

              request_message = file_proto.message_type.add()
              request_message.name = "WatchInventoryRequest"
              request_field = request_message.field.add()
              request_field.name = "sku"
              request_field.number = 1
              request_field.label = descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
              request_field.type = descriptor_pb2.FieldDescriptorProto.TYPE_STRING

              response_message = file_proto.message_type.add()
              response_message.name = "InventoryEvent"
              response_sku = response_message.field.add()
              response_sku.name = "sku"
              response_sku.number = 1
              response_sku.label = descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
              response_sku.type = descriptor_pb2.FieldDescriptorProto.TYPE_STRING
              response_status = response_message.field.add()
              response_status.name = "status"
              response_status.number = 2
              response_status.label = descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
              response_status.type = descriptor_pb2.FieldDescriptorProto.TYPE_STRING

              service = file_proto.service.add()
              service.name = "InventoryService"
              method = service.method.add()
              method.name = "WatchInventory"
              method.input_type = ".catalog.v1.WatchInventoryRequest"
              method.output_type = ".catalog.v1.InventoryEvent"
              method.server_streaming = True

              descriptor_db = DescriptorDatabase()
              descriptor_db.Add(file_proto)
              pool = DescriptorPool(descriptor_db)
              request_class = GetMessageClass(
                  pool.FindMessageTypeByName("catalog.v1.WatchInventoryRequest")
              )
              response_class = GetMessageClass(
                  pool.FindMessageTypeByName("catalog.v1.InventoryEvent")
              )

              def watch_inventory(request, context):
                  for status in ("ready", "restocked"):
                      response = response_class()
                      response.sku = request.sku
                      response.status = status
                      yield response

              handler = grpc.unary_stream_rpc_method_handler(
                  watch_inventory,
                  request_deserializer=request_class.FromString,
                  response_serializer=lambda message: message.SerializeToString(),
              )

              server = grpc.server(futures.ThreadPoolExecutor(max_workers=4))
              server.add_generic_rpc_handlers(
                  (
                      grpc.method_handlers_generic_handler(
                          "catalog.v1.InventoryService",
                          {"WatchInventory": handler},
                      ),
                  )
              )
              reflection.enable_server_reflection(
                  ("catalog.v1.InventoryService", reflection.SERVICE_NAME),
                  server,
                  pool=pool,
              )
              server.add_insecure_port("[::]:50051")
              server.start()
              server.wait_for_termination()
              PY
          ports:
            - containerPort: 50051
              name: grpc
YAML

"${KUBECTL}" rollout status -n "${NAMESPACE}" deployment/grpc-stream-upstream --timeout="${WAIT_TIMEOUT_SECONDS}s"
"${KUBECTL}" rollout status -n "${NAMESPACE}" deployment/"${RUNTIME_DEPLOYMENT_NAME}" --timeout="${WAIT_TIMEOUT_SECONDS}s"
wait_for_single_ready_pod "app=grpc-stream-upstream" "grpc-stream-upstream"
wait_for_single_ready_pod "app.kubernetes.io/name=${RUNTIME_SERVICE_NAME}" "${RUNTIME_SERVICE_NAME}"

"${KUBECTL}" apply -n "${NAMESPACE}" -f - <<YAML
apiVersion: batch/v1
kind: Job
metadata:
  name: grpc-stream-smoke-runner
spec:
  backoffLimit: 0
  template:
    spec:
      restartPolicy: Never
      containers:
        - name: runner
          image: ${RUNTIME_IMAGE}
          imagePullPolicy: ${IMAGE_PULL_POLICY}
          env:
            - name: RUNTIME_BASE_URL
              value: http://${RUNTIME_SERVICE_NAME}:8003
          command:
            - sh
            - -lc
            - |
              python - <<'PY'
              import asyncio
              import json
              import os
              from pathlib import Path

              import httpx

              from apps.compiler_worker.activities import build_streamable_http_tool_invoker
              from apps.mcp_runtime.loader import load_service_ir
              from libs.validator import PostDeployValidator

              async def wait_until_ready(base_url: str) -> None:
                  async with httpx.AsyncClient(timeout=5.0, follow_redirects=True) as client:
                      last_error = None
                      for _ in range(60):
                          try:
                              health = await client.get(f"{base_url}/healthz")
                              ready = await client.get(f"{base_url}/readyz")
                              if health.status_code == 200 and ready.status_code == 200:
                                  return
                              last_error = f"healthz={health.status_code}, readyz={ready.status_code}"
                          except httpx.RequestError as exc:
                              last_error = str(exc)
                          await asyncio.sleep(2)
                  raise RuntimeError(f"Runtime failed to become ready: {last_error}")

              async def main() -> None:
                  base_url = os.environ["RUNTIME_BASE_URL"]
                  await wait_until_ready(base_url)
                  service_ir = load_service_ir(Path("/config/service-ir.json"))
                  tool_invoker = build_streamable_http_tool_invoker(base_url)
                  direct_result = await tool_invoker(
                      "watchInventory",
                      {"payload": {"sku": "sku-live"}},
                  )
                  async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                      validator = PostDeployValidator(
                          client=client,
                          tool_invoker=build_streamable_http_tool_invoker(base_url),
                      )
                      report = await validator.validate(
                          base_url,
                          service_ir,
                          sample_invocations={
                              "watchInventory": {"payload": {"sku": "sku-live"}}
                          },
                      )

                  payload = {
                      "status": "ok" if report.overall_passed else "failed",
                      "direct_result": direct_result,
                      "report": report.model_dump(mode="json"),
                  }
                  print(json.dumps(payload, indent=2, sort_keys=True, default=str))

                  if not report.overall_passed:
                      raise SystemExit(1)
                  if direct_result.get("status") != "ok":
                      raise SystemExit(1)
                  if direct_result.get("transport") != "grpc_stream":
                      raise SystemExit(1)

                  stream_result = direct_result.get("result", {})
                  events = stream_result.get("events")
                  lifecycle = stream_result.get("lifecycle")
                  if not isinstance(events, list) or len(events) != 1:
                      raise SystemExit(1)
                  if not isinstance(lifecycle, dict):
                      raise SystemExit(1)
                  if lifecycle.get("messages_collected") != 1:
                      raise SystemExit(1)
                  parsed_data = events[0].get("parsed_data", {})
                  if parsed_data.get("sku") != "sku-live":
                      raise SystemExit(1)
                  if parsed_data.get("status") != "ready":
                      raise SystemExit(1)

              asyncio.run(main())
              PY
          volumeMounts:
            - name: service-ir
              mountPath: /config
              readOnly: true
      volumes:
        - name: service-ir
          configMap:
            name: ${RUNTIME_CONFIGMAP_NAME}
            items:
              - key: service-ir.json
                path: service-ir.json
YAML

"${KUBECTL}" wait -n "${NAMESPACE}" --for=condition=complete job/grpc-stream-smoke-runner --timeout="${WAIT_TIMEOUT_SECONDS}s"
"${KUBECTL}" logs -n "${NAMESPACE}" job/grpc-stream-smoke-runner
