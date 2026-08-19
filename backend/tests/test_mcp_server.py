from __future__ import annotations

import importlib
import inspect
import json
from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client, FastMCP

from customer_signal.analytics.service import AnalyticsService
from customer_signal.data.repository import DuckDBRepository


START_AT = "2026-07-20T00:00:00+09:00"
END_AT = "2026-08-19T00:00:00+09:00"
ALL_SOURCES = ["search_history", "search_feedback", "voc"]
EXPECTED_MATCHES = [
    "CUST-003",
    "CUST-007",
    "CUST-011",
    "CUST-016",
    "CUST-022",
    "CUST-028",
]
TOOL_NAMES = [
    "catalog_sources",
    "aggregate_events",
    "match_journey_pattern",
    "rank_customers",
    "get_customer_journey",
    "get_evidence",
]
READ_ONLY_ANNOTATIONS = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}
TIME_PROPERTIES = {
    "start_at": {"format": "date-time", "type": "string"},
    "end_at": {"format": "date-time", "type": "string"},
}
SOURCE_PROPERTY = {
    "items": {"enum": ALL_SOURCES, "type": "string"},
    "maxItems": 3,
    "minItems": 1,
    "type": "array",
}
LIMIT_PROPERTY = {
    "default": 100,
    "maximum": 100,
    "minimum": 1,
    "type": "integer",
}


@pytest.fixture
def analytics_service(repository: DuckDBRepository) -> AnalyticsService:
    return AnalyticsService(repository)


def _create_server(service: AnalyticsService) -> FastMCP:
    try:
        module = importlib.import_module("customer_signal.mcp_server")
    except ModuleNotFoundError:
        pytest.fail("customer_signal.mcp_server must provide the MCP adapter")
    try:
        create_mcp_server = module.create_mcp_server
    except AttributeError:
        pytest.fail("customer_signal.mcp_server.create_mcp_server is required")
    return create_mcp_server(service)


def _schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "additionalProperties": False,
        "properties": properties,
        "required": required,
        "type": "object",
    }


def _tool_error_text(result) -> str:
    return "\n".join(
        block.text for block in result.content if getattr(block, "type", None) == "text"
    )


async def test_server_lists_only_bounded_read_only_tools_with_structured_schemas(
    analytics_service: AnalyticsService,
):
    server = _create_server(analytics_service)
    expected_input_schemas = {
        "catalog_sources": _schema(TIME_PROPERTIES, ["start_at", "end_at"]),
        "aggregate_events": _schema(
            {
                **TIME_PROPERTIES,
                "enabled_sources": SOURCE_PROPERTY,
                "group_by": {
                    "default": "source",
                    "enum": ["source", "topic", "outcome"],
                    "type": "string",
                },
                "limit": LIMIT_PROPERTY,
            },
            ["start_at", "end_at", "enabled_sources"],
        ),
        "match_journey_pattern": _schema(
            {
                **TIME_PROPERTIES,
                "enabled_sources": SOURCE_PROPERTY,
                "limit": LIMIT_PROPERTY,
            },
            ["start_at", "end_at", "enabled_sources"],
        ),
        "rank_customers": _schema(
            {
                **TIME_PROPERTIES,
                "enabled_sources": SOURCE_PROPERTY,
                "limit": LIMIT_PROPERTY,
            },
            ["start_at", "end_at", "enabled_sources"],
        ),
        "get_customer_journey": _schema(
            {
                "customer_id": {"minLength": 1, "type": "string"},
                **TIME_PROPERTIES,
                "enabled_sources": SOURCE_PROPERTY,
                "limit": LIMIT_PROPERTY,
            },
            ["customer_id", "start_at", "end_at", "enabled_sources"],
        ),
        "get_evidence": _schema(
            {
                "evidence_ids": {
                    "items": {"minLength": 1, "type": "string"},
                    "maxItems": 100,
                    "minItems": 1,
                    "type": "array",
                }
            },
            ["evidence_ids"],
        ),
    }
    expected_output_fields = {
        "catalog_sources": {"result_id", "sources", "missing_sources", "stats"},
        "aggregate_events": {"result_id", "group_by", "buckets", "evidence_ids", "stats"},
        "match_journey_pattern": {
            "result_id",
            "candidate_count",
            "customer_count",
            "customer_ids",
            "customers",
            "missing_sources",
            "evidence_ids",
            "stats",
        },
        "rank_customers": {
            "result_id",
            "candidate_count",
            "customer_count",
            "customers",
            "evidence_ids",
            "stats",
        },
        "get_customer_journey": {
            "result_id",
            "customer_id",
            "events",
            "evidence_ids",
            "stats",
        },
        "get_evidence": {"result_id", "records", "evidence_ids", "stats"},
    }

    assert server.name == "Customer Signal Data"
    async with Client(server) as client:
        tools = await client.list_tools()

    assert [tool.name for tool in tools] == TOOL_NAMES
    for tool in tools:
        assert tool.annotations is not None
        assert tool.annotations.model_dump(exclude_none=True) == READ_ONLY_ANNOTATIONS
        assert tool.inputSchema == expected_input_schemas[tool.name]
        assert tool.outputSchema is not None
        assert tool.outputSchema["type"] == "object"
        assert tool.outputSchema["additionalProperties"] is False
        assert set(tool.outputSchema["properties"]) == expected_output_fields[tool.name]
        assert tool.description

    descriptions = {tool.name: tool.description for tool in tools}
    for name in TOOL_NAMES[:5]:
        assert "[start_at, end_at)" in descriptions[name]
    for name in TOOL_NAMES[1:5]:
        assert "allowlist" in descriptions[name]
        assert "1 to 100" in descriptions[name]
    assert "1 to 100" in descriptions["get_evidence"]
    assert "masked" in descriptions["get_evidence"]


async def test_all_six_tools_call_seeded_service_and_return_structured_data(
    analytics_service: AnalyticsService,
):
    server = _create_server(analytics_service)
    scope = {
        "start_at": START_AT,
        "end_at": END_AT,
        "enabled_sources": ALL_SOURCES,
    }

    async with Client(server) as client:
        catalog = await client.call_tool(
            "catalog_sources",
            {"start_at": START_AT, "end_at": END_AT},
        )
        aggregate = await client.call_tool(
            "aggregate_events",
            {**scope, "group_by": "source"},
        )
        matched = await client.call_tool("match_journey_pattern", scope)
        ranked = await client.call_tool("rank_customers", {**scope, "limit": 8})
        journey = await client.call_tool(
            "get_customer_journey",
            {
                **scope,
                "customer_id": "CUST-003",
                "enabled_sources": ["search_history", "voc"],
            },
        )
        evidence = await client.call_tool(
            "get_evidence",
            {"evidence_ids": journey.structured_content["evidence_ids"][:2]},
        )

    results = [catalog, aggregate, matched, ranked, journey, evidence]
    assert all(not result.is_error for result in results)
    assert all(result.data is not None for result in results)
    assert all(isinstance(result.structured_content, dict) for result in results)

    assert [source["source_id"] for source in catalog.structured_content["sources"]] == (
        ALL_SOURCES
    )
    assert [
        (bucket["value"], bucket["event_count"])
        for bucket in aggregate.structured_content["buckets"]
    ] == [
        ("search_history", 54),
        ("search_feedback", 30),
        ("voc", 24),
    ]
    assert aggregate.structured_content["stats"] == {
        "scanned_rows": 108,
        "returned_rows": 3,
    }
    assert matched.structured_content["customer_ids"] == EXPECTED_MATCHES
    assert matched.structured_content["customer_count"] == 6

    ranked_customers = ranked.structured_content["customers"]
    ordering = [(-customer["risk_score"], customer["customer_id"]) for customer in ranked_customers]
    assert ordering == sorted(ordering)
    assert ranked.structured_content["customer_count"] == 8

    journey_events = journey.structured_content["events"]
    assert [event["source_id"] for event in journey_events] == [
        "search_history",
        "search_history",
        "voc",
    ]
    assert [record["evidence_id"] for record in evidence.structured_content["records"]] == (
        journey.structured_content["evidence_ids"][:2]
    )
    for record in evidence.structured_content["records"]:
        serialized = json.dumps(record, ensure_ascii=False)
        assert record["masked_customer_id"].startswith("CU***")
        assert record["raw_fields"]["customer_ref"] == record["masked_customer_id"]
        assert "CUST-" not in serialized


async def test_invalid_inputs_are_mcp_errors_and_internal_details_are_masked(
    analytics_service: AnalyticsService,
):
    server = _create_server(analytics_service)
    async with Client(server) as client:
        invalid_source = await client.call_tool(
            "aggregate_events",
            {
                "start_at": START_AT,
                "end_at": END_AT,
                "enabled_sources": ["billing"],
            },
            raise_on_error=False,
        )
        invalid_limit = await client.call_tool(
            "rank_customers",
            {
                "start_at": START_AT,
                "end_at": END_AT,
                "enabled_sources": ALL_SOURCES,
                "limit": 101,
            },
            raise_on_error=False,
        )
        invalid_time = await client.call_tool(
            "catalog_sources",
            {"start_at": END_AT, "end_at": START_AT},
            raise_on_error=False,
        )
        unknown_evidence = await client.call_tool(
            "get_evidence",
            {"evidence_ids": ["EVD-INTERNAL-UNKNOWN"]},
            raise_on_error=False,
        )

    assert invalid_source.is_error
    assert "Input validation error" in _tool_error_text(invalid_source)
    assert invalid_limit.is_error
    assert "Input validation error" in _tool_error_text(invalid_limit)

    assert invalid_time.is_error
    assert _tool_error_text(invalid_time) == "Error calling tool 'catalog_sources'"
    assert "start_at must be before end_at" not in _tool_error_text(invalid_time)

    assert unknown_evidence.is_error
    assert _tool_error_text(unknown_evidence) == "Error calling tool 'get_evidence'"
    assert "EVD-INTERNAL-UNKNOWN" not in _tool_error_text(unknown_evidence)


async def test_server_exposes_no_free_query_or_arbitrary_sql_tool(
    analytics_service: AnalyticsService,
):
    server = _create_server(analytics_service)
    async with Client(server) as client:
        tools = await client.list_tools()
        result = await client.call_tool(
            "query_sql",
            {"sql": "select * from events"},
            raise_on_error=False,
        )

    assert [tool.name for tool in tools] == TOOL_NAMES
    assert result.is_error


def test_mcp_adapter_source_stays_thin_and_storage_agnostic(
    analytics_service: AnalyticsService,
):
    server = _create_server(analytics_service)
    module = importlib.import_module("customer_signal.mcp_server")
    source_path = Path(inspect.getfile(module))
    source = source_path.read_text()
    lowered = source.lower()

    assert isinstance(server, FastMCP)
    assert "duckdb" not in lowered
    assert "select " not in lowered
    assert "duckdbrepository" not in lowered
    assert "analyticsservice(" not in lowered
    assert "failed_search_score" not in lowered
    assert "risk_level_for_score" not in lowered
