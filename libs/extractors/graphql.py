"""GraphQL extractor based on schema introspection payloads."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse, urlunparse

import httpx

from libs.extractors.base import SourceConfig
from libs.ir.models import (
    AuthConfig,
    AuthType,
    ErrorSchema,
    EventDescriptor,
    EventDirection,
    EventSupportLevel,
    EventTransport,
    GraphQLOperationConfig,
    GraphQLOperationType,
    Operation,
    Param,
    RiskLevel,
    RiskMetadata,
    ServiceIR,
    SourceType,
)

JSONDict = dict[str, Any]
_INTROSPECTION_QUERY = """
query IntrospectionQuery {
  __schema {
    description
    queryType { name }
    mutationType { name }
    types {
      kind
      name
      description
      fields {
        name
        description
        args {
          name
          description
          defaultValue
          type { kind name ofType { kind name ofType { kind name ofType { kind name } } } }
        }
        type { kind name ofType { kind name ofType { kind name ofType { kind name } } } }
      }
      inputFields {
        name
        description
        defaultValue
        type { kind name ofType { kind name ofType { kind name ofType { kind name } } } }
      }
      enumValues { name }
    }
  }
}
""".strip()

_SCALAR_TYPE_MAP = {
    "Int": "integer",
    "Float": "number",
    "Boolean": "boolean",
    "ID": "string",
    "String": "string",
}


class GraphQLExtractor:
    """Extract GraphQL queries and mutations into ServiceIR."""

    protocol_name: str = "graphql"

    def detect(self, source: SourceConfig) -> float:
        if source.file_content or source.file_path:
            content = self._get_content(source)
            if content is None:
                return 0.0
            try:
                schema = self._parse_schema(content)
            except Exception:
                return 0.0
            return 0.95 if schema else 0.0

        if source.url and "graphql" in source.url.lower():
            return 0.4
        return 0.0

    def extract(self, source: SourceConfig) -> ServiceIR:
        content = self._get_content(source)
        if content is None:
            raise ValueError("Could not read GraphQL schema source")

        schema = self._parse_schema(content)
        types = schema.get("types", [])
        type_index = {
            graphql_type["name"]: graphql_type
            for graphql_type in types
            if isinstance(graphql_type, dict) and isinstance(graphql_type.get("name"), str)
        }
        operations = self._extract_operations(schema, type_index, source)
        service_name = self._derive_service_name(source)
        ignored_subscriptions = self._subscription_field_names(schema, type_index)
        graphql_path = self._graphql_path(source)
        metadata: dict[str, Any] = {
            "query_type": self._root_type_name(schema, "queryType"),
            "mutation_type": self._root_type_name(schema, "mutationType"),
            "subscription_type": self._root_type_name(schema, "subscriptionType"),
            "source_format": "introspection_json",
        }
        if ignored_subscriptions:
            metadata["ignored_subscriptions"] = ignored_subscriptions

        return ServiceIR(
            source_url=source.url,
            source_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            protocol="graphql",
            service_name=service_name,
            service_description=str(schema.get("description", "")),
            base_url=self._graphql_base_url(source),
            auth=self._derive_auth(source),
            operations=operations,
            event_descriptors=[
                EventDescriptor(
                    id=field_name,
                    name=field_name,
                    transport=EventTransport.graphql_subscription,
                    direction=EventDirection.inbound,
                    support=EventSupportLevel.unsupported,
                    channel=graphql_path,
                )
                for field_name in ignored_subscriptions
            ],
            metadata=metadata,
        )

    def _get_content(self, source: SourceConfig) -> str | None:
        if source.file_content:
            return source.file_content
        if source.file_path:
            return Path(source.file_path).read_text(encoding="utf-8")
        if source.url:
            response = httpx.post(
                source.url,
                json={"query": _INTROSPECTION_QUERY},
                headers=self._auth_headers(source),
                timeout=30,
            )
            response.raise_for_status()
            return response.text
        return None

    def _parse_schema(self, content: str) -> JSONDict:
        payload = json.loads(content)
        if "__schema" in payload and isinstance(payload["__schema"], dict):
            return cast(JSONDict, payload["__schema"])
        data = payload.get("data")
        if isinstance(data, dict) and isinstance(data.get("__schema"), dict):
            return cast(JSONDict, data["__schema"])
        raise ValueError("Payload is not a GraphQL introspection response")

    def _extract_operations(
        self,
        schema: JSONDict,
        type_index: dict[str, JSONDict],
        source: SourceConfig,
    ) -> list[Operation]:
        operations: list[Operation] = []
        operations.extend(
            self._extract_root_operations(
                root_type_name=self._root_type_name(schema, "queryType"),
                operation_kind="query",
                type_index=type_index,
                source=source,
            )
        )
        operations.extend(
            self._extract_root_operations(
                root_type_name=self._root_type_name(schema, "mutationType"),
                operation_kind="mutation",
                type_index=type_index,
                source=source,
            )
        )
        return operations

    def _extract_root_operations(
        self,
        *,
        root_type_name: str | None,
        operation_kind: str,
        type_index: dict[str, JSONDict],
        source: SourceConfig,
    ) -> list[Operation]:
        if root_type_name is None:
            return []

        root_type = type_index.get(root_type_name)
        if root_type is None:
            return []

        fields = root_type.get("fields", [])
        if not isinstance(fields, list):
            return []

        risk_level = RiskLevel.safe if operation_kind == "query" else RiskLevel.cautious
        graphql_path = self._graphql_path(source)
        operations: list[Operation] = []
        for field in fields:
            if not isinstance(field, dict):
                continue
            field_name = field.get("name")
            if not isinstance(field_name, str):
                continue

            args = field.get("args", [])
            params = [
                self._param_from_argument(argument, type_index)
                for argument in args
                if isinstance(argument, dict)
            ]
            operations.append(
                Operation(
                    id=field_name,
                    name=field_name,
                    description=str(field.get("description", "")),
                    method="POST",
                    path=graphql_path,
                    params=params,
                    graphql=self._build_graphql_operation_config(
                        field_name=field_name,
                        operation_kind=operation_kind,
                        field=field,
                        type_index=type_index,
                    ),
                    risk=RiskMetadata(
                        writes_state=operation_kind == "mutation",
                        destructive=False,
                        external_side_effect=operation_kind == "mutation",
                        idempotent=operation_kind == "query",
                        risk_level=risk_level,
                        confidence=0.95,
                        source=SourceType.extractor,
                    ),
                    tags=["graphql", operation_kind],
                    source=SourceType.extractor,
                    confidence=0.95,
                    enabled=True,
                    error_schema=ErrorSchema(
                        default_error_schema={
                            "type": "object",
                            "properties": {
                                "errors": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "message": {"type": "string"},
                                            "locations": {"type": "array"},
                                            "path": {"type": "array"},
                                        },
                                        "required": ["message"],
                                    },
                                }
                            },
                        }
                    ),
                )
            )

        return operations

    def _build_graphql_operation_config(
        self,
        *,
        field_name: str,
        operation_kind: str,
        field: JSONDict,
        type_index: dict[str, JSONDict],
    ) -> GraphQLOperationConfig:
        args = field.get("args", [])
        variable_names: list[str] = []
        variable_definitions: list[str] = []
        argument_bindings: list[str] = []
        for argument in args:
            if not isinstance(argument, dict):
                continue
            argument_name = argument.get("name")
            if not isinstance(argument_name, str):
                continue
            variable_names.append(argument_name)
            variable_type = self._graphql_type_literal(argument.get("type"))
            variable_definitions.append(f"${argument_name}: {variable_type}")
            argument_bindings.append(f"{argument_name}: ${argument_name}")

        variable_block = f"({', '.join(variable_definitions)})" if variable_definitions else ""
        argument_block = f"({', '.join(argument_bindings)})" if argument_bindings else ""
        selection_set = self._selection_set_for_type(field.get("type"), type_index)
        invocation = f"{field_name}{argument_block}"
        if selection_set is not None:
            invocation = f"{invocation} {selection_set}"
        document = f"{operation_kind} {field_name}{variable_block} {{\n  {invocation}\n}}"
        return GraphQLOperationConfig(
            operation_type=GraphQLOperationType(operation_kind),
            operation_name=field_name,
            document=document,
            variable_names=variable_names,
        )

    def _param_from_argument(
        self,
        argument: JSONDict,
        type_index: dict[str, JSONDict],
    ) -> Param:
        type_ref = argument.get("type")
        return Param(
            name=str(argument.get("name", "arg")),
            type=self._map_type(type_ref, type_index),
            required=self._is_required(type_ref),
            description=str(argument.get("description", "")),
            default=self._parse_default_value(argument.get("defaultValue")),
            source=SourceType.extractor,
            confidence=0.95,
        )

    def _map_type(self, type_ref: Any, type_index: dict[str, JSONDict]) -> str:
        if self._contains_kind(type_ref, "LIST"):
            return "array"

        named_type = self._unwrap_named_type(type_ref)
        kind = str(named_type.get("kind", "SCALAR"))
        name = str(named_type.get("name", "String"))

        if kind == "SCALAR":
            return _SCALAR_TYPE_MAP.get(name, "string")
        if kind == "ENUM":
            return "string"
        if kind == "INPUT_OBJECT":
            return "object"
        if kind == "OBJECT":
            return "object"

        referenced_type = type_index.get(name)
        if referenced_type is not None and referenced_type.get("kind") == "ENUM":
            return "string"
        if referenced_type is not None and referenced_type.get("kind") == "INPUT_OBJECT":
            return "object"
        return "string"

    def _contains_kind(self, type_ref: Any, target_kind: str) -> bool:
        current = type_ref
        while isinstance(current, dict):
            if current.get("kind") == target_kind:
                return True
            current = current.get("ofType")
        return False

    def _unwrap_named_type(self, type_ref: Any) -> JSONDict:
        current = type_ref
        last_dict: JSONDict = {}
        while isinstance(current, dict):
            last_dict = current
            if current.get("name") is not None:
                return current
            current = current.get("ofType")
        return last_dict

    def _is_required(self, type_ref: Any) -> bool:
        return isinstance(type_ref, dict) and type_ref.get("kind") == "NON_NULL"

    def _parse_default_value(self, default_value: Any) -> Any:
        if default_value is None:
            return None
        if not isinstance(default_value, str):
            return default_value
        try:
            return json.loads(default_value)
        except json.JSONDecodeError:
            stripped = default_value.strip('"')
            return stripped

    def _graphql_type_literal(self, type_ref: Any) -> str:
        if not isinstance(type_ref, dict):
            return "String"

        kind = type_ref.get("kind")
        if kind == "NON_NULL":
            return f"{self._graphql_type_literal(type_ref.get('ofType'))}!"
        if kind == "LIST":
            return f"[{self._graphql_type_literal(type_ref.get('ofType'))}]"

        name = type_ref.get("name")
        if isinstance(name, str) and name:
            return name

        nested = type_ref.get("ofType")
        if isinstance(nested, dict):
            return self._graphql_type_literal(nested)
        return "String"

    def _selection_set_for_type(
        self,
        type_ref: Any,
        type_index: dict[str, JSONDict],
        *,
        visited: set[str] | None = None,
        depth: int = 0,
        max_depth: int = 2,
    ) -> str | None:
        named_type = self._unwrap_named_type(type_ref)
        kind = str(named_type.get("kind", "SCALAR"))
        name = named_type.get("name")
        if kind in {"SCALAR", "ENUM"} or not isinstance(name, str) or not name:
            return None

        referenced_type = type_index.get(name)
        if referenced_type is None or referenced_type.get("kind") not in {"OBJECT", "INTERFACE"}:
            return None

        next_visited = set(visited or ())
        if name in next_visited:
            return "{ __typename }"
        next_visited.add(name)

        fields = referenced_type.get("fields", [])
        if not isinstance(fields, list):
            return "{ __typename }"

        selections: list[str] = []
        for child_field in fields:
            if not isinstance(child_field, dict):
                continue
            child_name = child_field.get("name")
            if not isinstance(child_name, str):
                continue
            child_type = child_field.get("type")
            if self._is_leaf_output_type(child_type, type_index):
                selections.append(child_name)
                continue
            if depth >= max_depth:
                continue
            child_selection = self._selection_set_for_type(
                child_type,
                type_index,
                visited=next_visited,
                depth=depth + 1,
                max_depth=max_depth,
            )
            if child_selection is not None:
                selections.append(f"{child_name} {child_selection}")

        if not selections:
            return "{ __typename }"
        return "{ " + " ".join(selections) + " }"

    def _is_leaf_output_type(
        self,
        type_ref: Any,
        type_index: dict[str, JSONDict],
    ) -> bool:
        named_type = self._unwrap_named_type(type_ref)
        kind = str(named_type.get("kind", "SCALAR"))
        if kind in {"SCALAR", "ENUM"}:
            return True
        name = named_type.get("name")
        if isinstance(name, str):
            referenced_type = type_index.get(name)
            return referenced_type is not None and referenced_type.get("kind") == "ENUM"
        return False

    def _derive_service_name(self, source: SourceConfig) -> str:
        hint_name = source.hints.get("service_name")
        if hint_name:
            return _slugify(hint_name)
        if source.file_path:
            return _slugify(Path(source.file_path).stem)
        if source.url:
            parsed = urlparse(source.url)
            if parsed.netloc:
                return _slugify(parsed.netloc.split(":")[0])
        return "graphql-service"

    def _derive_auth(self, source: SourceConfig) -> AuthConfig:
        if source.auth_header:
            return AuthConfig(
                type=AuthType.bearer,
                header_name="Authorization",
                header_prefix="Bearer",
            )
        if source.auth_token:
            return AuthConfig(
                type=AuthType.bearer,
                header_name="Authorization",
                header_prefix="Bearer",
            )
        return AuthConfig(type=AuthType.none)

    def _auth_headers(self, source: SourceConfig) -> dict[str, str]:
        if source.auth_header:
            return {"Authorization": source.auth_header}
        if source.auth_token:
            return {"Authorization": f"Bearer {source.auth_token}"}
        return {}

    def _root_type_name(self, schema: JSONDict, key: str) -> str | None:
        root_type = schema.get(key)
        if isinstance(root_type, dict) and isinstance(root_type.get("name"), str):
            return cast(str, root_type["name"])
        return None

    def _graphql_path(self, source: SourceConfig) -> str:
        return str(
            source.hints.get(
                "graphql_path",
                urlparse(source.url or "").path or "/graphql",
            )
        )

    def _graphql_base_url(self, source: SourceConfig) -> str:
        hinted_base_url = source.hints.get("base_url")
        if isinstance(hinted_base_url, str) and hinted_base_url:
            return hinted_base_url

        if source.url:
            parsed = urlparse(source.url)
            if parsed.scheme and parsed.netloc:
                return urlunparse((parsed.scheme, parsed.netloc, "", "", "", ""))
        return "http://localhost"

    def _subscription_field_names(
        self,
        schema: JSONDict,
        type_index: dict[str, JSONDict],
    ) -> list[str]:
        subscription_type_name = self._root_type_name(schema, "subscriptionType")
        if subscription_type_name is None:
            return []
        subscription_type = type_index.get(subscription_type_name)
        if subscription_type is None:
            return []
        fields = subscription_type.get("fields", [])
        if not isinstance(fields, list):
            return []
        subscription_fields = [
            str(field["name"])
            for field in fields
            if isinstance(field, dict) and isinstance(field.get("name"), str)
        ]
        subscription_fields.sort()
        return subscription_fields


def _slugify(text: str) -> str:
    import re

    normalized = text.lower().strip()
    normalized = re.sub(r"[^a-z0-9]+", "-", normalized)
    return normalized.strip("-")
