"""OData v4 $metadata extractor — parses EDMX metadata and generates CRUD operations."""

from __future__ import annotations

import hashlib
import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from libs.extractors.base import SourceConfig
from libs.ir.models import (
    AuthConfig,
    AuthType,
    ErrorSchema,
    Operation,
    Param,
    RiskLevel,
    RiskMetadata,
    ServiceIR,
    SourceType,
)

logger = logging.getLogger(__name__)

EDMX_NS = "http://docs.oasis-open.org/odata/ns/edmx"
EDM_NS = "http://docs.oasis-open.org/odata/ns/edm"
NS = {"edmx": EDMX_NS, "edm": EDM_NS}

EDM_TYPE_MAP: dict[str, str] = {
    "Edm.String": "string",
    "Edm.Int32": "integer",
    "Edm.Int64": "integer",
    "Edm.Int16": "integer",
    "Edm.Byte": "integer",
    "Edm.Decimal": "number",
    "Edm.Double": "number",
    "Edm.Single": "number",
    "Edm.Boolean": "boolean",
    "Edm.DateTimeOffset": "string",
    "Edm.Date": "string",
    "Edm.TimeOfDay": "string",
    "Edm.Guid": "string",
}

ODATA_ERROR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "error": {
            "type": "object",
            "properties": {
                "code": {"type": "string"},
                "message": {"type": "string"},
            },
            "required": ["code", "message"],
        },
    },
    "required": ["error"],
}


@dataclass(frozen=True)
class EntityProperty:
    """A single property of an OData EntityType."""

    name: str
    type_name: str
    nullable: bool
    is_key: bool


@dataclass(frozen=True)
class EntityTypeInfo:
    """Parsed OData EntityType definition."""

    name: str
    properties: list[EntityProperty]

    @property
    def key_properties(self) -> list[EntityProperty]:
        return [p for p in self.properties if p.is_key]

    @property
    def non_key_properties(self) -> list[EntityProperty]:
        return [p for p in self.properties if not p.is_key]


class ODataExtractor:
    """Extract OData v4 $metadata into ServiceIR."""

    protocol_name: str = "odata"

    def detect(self, source: SourceConfig) -> float:
        if source.hints.get("protocol") == "odata":
            return 1.0

        path_or_url = source.file_path or source.url or ""
        if path_or_url.endswith("$metadata"):
            return 0.95

        content = self._get_content(source)
        if content and "<edmx:Edmx" in content:
            return 0.9

        return 0.0

    def extract(self, source: SourceConfig) -> ServiceIR:
        content = self._get_content(source)
        if content is None:
            raise ValueError("Could not read source content")

        root = ET.fromstring(content)
        if _local_name(root.tag) != "Edmx":
            raise ValueError("OData extractor requires an EDMX metadata document.")

        source_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        odata_version = root.attrib.get("Version", "4.0")

        schema = root.find("edmx:DataServices/edm:Schema", NS)
        if schema is None:
            raise ValueError("No Schema element found in EDMX document.")

        namespace = schema.attrib.get("Namespace", "")

        entity_types = _parse_entity_types(schema)
        entity_type_map = {et.name: et for et in entity_types}

        container = schema.find("edm:EntityContainer", NS)
        if container is None:
            raise ValueError("No EntityContainer found in EDMX schema.")

        entity_sets = _parse_entity_sets(container)
        function_imports = _parse_function_imports(container)
        action_imports = _parse_action_imports(container)
        functions = _parse_functions(schema)
        actions = _parse_actions(schema)

        base_url = source.url or "https://odata-service"
        # Strip $metadata suffix from base URL
        if base_url.endswith("/$metadata"):
            base_url = base_url[: -len("/$metadata")]
        elif base_url.endswith("$metadata"):
            base_url = base_url[: -len("$metadata")]

        service_name = _slugify(container.attrib.get("Name", "odata-service"))

        operations: list[Operation] = []

        for es_name, es_type_name in entity_sets.items():
            type_local = _strip_namespace(es_type_name, namespace)
            et_info = entity_type_map.get(type_local)
            if et_info is None:
                logger.warning("EntityType %s not found for EntitySet %s", es_type_name, es_name)
                continue
            operations.extend(_build_entity_set_operations(es_name, et_info))

        for fi_name in function_imports:
            func_info = functions.get(fi_name)
            params = func_info.get("params", []) if func_info else []
            operations.append(_build_function_import_operation(fi_name, params))

        for ai_name in action_imports:
            action_info = actions.get(ai_name)
            params = action_info.get("params", []) if action_info else []
            operations.append(_build_action_import_operation(ai_name, params))

        return ServiceIR(
            source_url=source.url,
            source_hash=source_hash,
            protocol="odata",
            service_name=service_name,
            service_description="OData v4 service extracted from EDMX metadata.",
            base_url=base_url,
            auth=AuthConfig(type=AuthType.none),
            operations=operations,
            metadata={
                "odata_version": odata_version,
                "schema_namespace": namespace,
                "entity_types": [et.name for et in entity_types],
                "entity_sets": list(entity_sets.keys()),
            },
        )

    def _get_content(self, source: SourceConfig) -> str | None:
        if source.file_content:
            return source.file_content
        if source.file_path:
            return Path(source.file_path).read_text(encoding="utf-8")
        if source.url:
            try:
                response = httpx.get(
                    source.url, timeout=30, headers=self._auth_headers(source)
                )
                response.raise_for_status()
                return response.text
            except Exception:
                logger.warning(
                    "Failed to fetch OData $metadata from %s", source.url, exc_info=True
                )
                return None
        return None

    def _auth_headers(self, source: SourceConfig) -> dict[str, str]:
        headers: dict[str, str] = {}
        if source.auth_header:
            headers["Authorization"] = source.auth_header
        elif source.auth_token:
            headers["Authorization"] = f"Bearer {source.auth_token}"
        return headers


# ── XML parsing helpers ────────────────────────────────────────────────────


def _parse_entity_types(schema: ET.Element) -> list[EntityTypeInfo]:
    entity_types: list[EntityTypeInfo] = []
    for et_elem in schema.findall("edm:EntityType", NS):
        name = et_elem.attrib.get("Name")
        if not name:
            continue

        key_names: set[str] = set()
        key_elem = et_elem.find("edm:Key", NS)
        if key_elem is not None:
            for prop_ref in key_elem.findall("edm:PropertyRef", NS):
                ref_name = prop_ref.attrib.get("Name")
                if ref_name:
                    key_names.add(ref_name)

        properties: list[EntityProperty] = []
        for prop_elem in et_elem.findall("edm:Property", NS):
            prop_name = prop_elem.attrib.get("Name")
            if not prop_name:
                continue
            properties.append(
                EntityProperty(
                    name=prop_name,
                    type_name=prop_elem.attrib.get("Type", "Edm.String"),
                    nullable=prop_elem.attrib.get("Nullable", "true").lower() != "false",
                    is_key=prop_name in key_names,
                )
            )

        entity_types.append(EntityTypeInfo(name=name, properties=properties))
    return entity_types


def _parse_entity_sets(container: ET.Element) -> dict[str, str]:
    """Return {EntitySetName: EntityTypeName}."""
    entity_sets: dict[str, str] = {}
    for es_elem in container.findall("edm:EntitySet", NS):
        name = es_elem.attrib.get("Name")
        entity_type = es_elem.attrib.get("EntityType")
        if name and entity_type:
            entity_sets[name] = entity_type
    return entity_sets


def _parse_function_imports(container: ET.Element) -> list[str]:
    return [
        fi.attrib["Name"]
        for fi in container.findall("edm:FunctionImport", NS)
        if "Name" in fi.attrib
    ]


def _parse_action_imports(container: ET.Element) -> list[str]:
    return [
        ai.attrib["Name"]
        for ai in container.findall("edm:ActionImport", NS)
        if "Name" in ai.attrib
    ]


def _parse_functions(schema: ET.Element) -> dict[str, dict[str, Any]]:
    """Parse <Function> elements and return {name: {params: [...]}}."""
    functions: dict[str, dict[str, Any]] = {}
    for func_elem in schema.findall("edm:Function", NS):
        name = func_elem.attrib.get("Name")
        if not name:
            continue
        params: list[dict[str, str]] = []
        for param_elem in func_elem.findall("edm:Parameter", NS):
            param_name = param_elem.attrib.get("Name")
            param_type = param_elem.attrib.get("Type", "Edm.String")
            if param_name:
                params.append({"name": param_name, "type": param_type})
        functions[name] = {"params": params}
    return functions


def _parse_actions(schema: ET.Element) -> dict[str, dict[str, Any]]:
    """Parse <Action> elements and return {name: {params: [...]}}."""
    actions: dict[str, dict[str, Any]] = {}
    for action_elem in schema.findall("edm:Action", NS):
        name = action_elem.attrib.get("Name")
        if not name:
            continue
        params: list[dict[str, str]] = []
        for param_elem in action_elem.findall("edm:Parameter", NS):
            param_name = param_elem.attrib.get("Name")
            param_type = param_elem.attrib.get("Type", "Edm.String")
            if param_name:
                params.append({"name": param_name, "type": param_type})
        actions[name] = {"params": params}
    return actions


# ── Operation builders ─────────────────────────────────────────────────────


def _build_entity_set_operations(
    entity_set_name: str, et_info: EntityTypeInfo
) -> list[Operation]:
    """Generate the 5 CRUD operations for an EntitySet."""
    ops: list[Operation] = []
    es_lower = entity_set_name.lower()
    key_props = et_info.key_properties
    key_name = key_props[0].name if key_props else "id"
    key_type = _edm_to_json_type(key_props[0].type_name) if key_props else "integer"

    odata_query_params = [
        Param(name="$filter", type="string", required=False,
              description="OData filter expression",
              source=SourceType.extractor, confidence=1.0),
        Param(name="$select", type="string", required=False,
              description="Comma-separated list of properties to include",
              source=SourceType.extractor, confidence=1.0),
        Param(name="$top", type="integer", required=False,
              description="Maximum number of items to return",
              source=SourceType.extractor, confidence=1.0),
        Param(name="$skip", type="integer", required=False,
              description="Number of items to skip",
              source=SourceType.extractor, confidence=1.0),
        Param(name="$orderby", type="string", required=False,
              description="Comma-separated list of properties to sort by",
              source=SourceType.extractor, confidence=1.0),
    ]

    # LIST
    ops.append(Operation(
        id=f"list_{es_lower}",
        name=f"List {entity_set_name}",
        description=f"List entities from {entity_set_name} with OData query options.",
        method="GET",
        path=f"/{entity_set_name}",
        params=odata_query_params,
        risk=_risk_safe(),
        tags=["odata", entity_set_name],
        source=SourceType.extractor,
        confidence=1.0,
        enabled=True,
        error_schema=ErrorSchema(default_error_schema=ODATA_ERROR_SCHEMA),
    ))

    # GET by key
    ops.append(Operation(
        id=f"get_{es_lower}_by_key",
        name=f"Get {entity_set_name} by key",
        description=f"Get a single entity from {entity_set_name} by its key.",
        method="GET",
        path=f"/{entity_set_name}({{{key_name}}})",
        params=[
            Param(name=key_name, type=key_type, required=True,
                  description=f"Key property {key_name}",
                  source=SourceType.extractor, confidence=1.0),
        ],
        risk=_risk_safe(),
        tags=["odata", entity_set_name],
        source=SourceType.extractor,
        confidence=1.0,
        enabled=True,
        error_schema=ErrorSchema(default_error_schema=ODATA_ERROR_SCHEMA),
    ))

    # CREATE
    create_params = [
        Param(
            name=p.name,
            type=_edm_to_json_type(p.type_name),
            required=True,
            description=f"Property {p.name}",
            source=SourceType.extractor,
            confidence=1.0,
        )
        for p in et_info.non_key_properties
    ]
    ops.append(Operation(
        id=f"create_{es_lower}",
        name=f"Create {entity_set_name}",
        description=f"Create a new entity in {entity_set_name}.",
        method="POST",
        path=f"/{entity_set_name}",
        params=create_params,
        risk=_risk_cautious(),
        tags=["odata", entity_set_name],
        source=SourceType.extractor,
        confidence=1.0,
        enabled=True,
        error_schema=ErrorSchema(default_error_schema=ODATA_ERROR_SCHEMA),
    ))

    # UPDATE
    update_params = [
        Param(name=key_name, type=key_type, required=True,
              description=f"Key property {key_name}",
              source=SourceType.extractor, confidence=1.0),
    ] + [
        Param(
            name=p.name,
            type=_edm_to_json_type(p.type_name),
            required=False,
            description=f"Property {p.name}",
            source=SourceType.extractor,
            confidence=1.0,
        )
        for p in et_info.non_key_properties
    ]
    ops.append(Operation(
        id=f"update_{es_lower}",
        name=f"Update {entity_set_name}",
        description=f"Update an entity in {entity_set_name} (partial update).",
        method="PATCH",
        path=f"/{entity_set_name}({{{key_name}}})",
        params=update_params,
        risk=_risk_cautious(),
        tags=["odata", entity_set_name],
        source=SourceType.extractor,
        confidence=1.0,
        enabled=True,
        error_schema=ErrorSchema(default_error_schema=ODATA_ERROR_SCHEMA),
    ))

    # DELETE
    ops.append(Operation(
        id=f"delete_{es_lower}",
        name=f"Delete {entity_set_name}",
        description=f"Delete an entity from {entity_set_name}.",
        method="DELETE",
        path=f"/{entity_set_name}({{{key_name}}})",
        params=[
            Param(name=key_name, type=key_type, required=True,
                  description=f"Key property {key_name}",
                  source=SourceType.extractor, confidence=1.0),
        ],
        risk=_risk_dangerous(),
        tags=["odata", entity_set_name],
        source=SourceType.extractor,
        confidence=1.0,
        enabled=True,
        error_schema=ErrorSchema(default_error_schema=ODATA_ERROR_SCHEMA),
    ))

    return ops


def _build_function_import_operation(
    name: str, params: list[dict[str, str]]
) -> Operation:
    """Build a GET operation for a FunctionImport."""
    ir_params = [
        Param(
            name=p["name"],
            type=_edm_to_json_type(p["type"]),
            required=True,
            description=f"Parameter {p['name']}",
            source=SourceType.extractor,
            confidence=1.0,
        )
        for p in params
    ]
    return Operation(
        id=f"func_{_snake_case(name)}",
        name=_humanize_identifier(name),
        description=f"OData function: {name}.",
        method="GET",
        path=f"/{name}",
        params=ir_params,
        risk=_risk_safe(),
        tags=["odata", "function"],
        source=SourceType.extractor,
        confidence=1.0,
        enabled=True,
        error_schema=ErrorSchema(default_error_schema=ODATA_ERROR_SCHEMA),
    )


def _build_action_import_operation(
    name: str, params: list[dict[str, str]]
) -> Operation:
    """Build a POST operation for an ActionImport."""
    ir_params = [
        Param(
            name=p["name"],
            type=_edm_to_json_type(p["type"]),
            required=True,
            description=f"Parameter {p['name']}",
            source=SourceType.extractor,
            confidence=1.0,
        )
        for p in params
    ]
    return Operation(
        id=f"action_{_snake_case(name)}",
        name=_humanize_identifier(name),
        description=f"OData action: {name}.",
        method="POST",
        path=f"/{name}",
        params=ir_params,
        risk=_risk_cautious(),
        tags=["odata", "action"],
        source=SourceType.extractor,
        confidence=1.0,
        enabled=True,
        error_schema=ErrorSchema(default_error_schema=ODATA_ERROR_SCHEMA),
    )


# ── Risk helpers ───────────────────────────────────────────────────────────


def _risk_safe() -> RiskMetadata:
    return RiskMetadata(
        writes_state=False,
        destructive=False,
        external_side_effect=False,
        idempotent=True,
        risk_level=RiskLevel.safe,
        confidence=0.9,
        source=SourceType.extractor,
    )


def _risk_cautious() -> RiskMetadata:
    return RiskMetadata(
        writes_state=True,
        destructive=False,
        external_side_effect=True,
        idempotent=False,
        risk_level=RiskLevel.cautious,
        confidence=0.9,
        source=SourceType.extractor,
    )


def _risk_dangerous() -> RiskMetadata:
    return RiskMetadata(
        writes_state=True,
        destructive=True,
        external_side_effect=True,
        idempotent=False,
        risk_level=RiskLevel.dangerous,
        confidence=0.9,
        source=SourceType.extractor,
    )


# ── Utility functions ──────────────────────────────────────────────────────


def _edm_to_json_type(edm_type: str) -> str:
    return EDM_TYPE_MAP.get(edm_type, "string")


def _strip_namespace(qualified_name: str, namespace: str) -> str:
    prefix = f"{namespace}."
    if qualified_name.startswith(prefix):
        return qualified_name[len(prefix) :]
    return qualified_name


def _local_name(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[1]
    if ":" in tag:
        return tag.split(":", 1)[1]
    return tag


def _humanize_identifier(value: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", " ", value).strip()


def _snake_case(value: str) -> str:
    s = re.sub(r"(?<!^)(?=[A-Z])", "_", value).lower()
    return re.sub(r"[^a-z0-9]+", "_", s).strip("_")


def _slugify(text: str) -> str:
    normalized = re.sub(r"(?<!^)(?=[A-Z])", "-", text).lower().strip()
    return re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")


__all__ = ["ODataExtractor"]
