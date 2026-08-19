"""Read-only MCP tools backed by the deterministic analytics service."""

from __future__ import annotations

from typing import Annotated

from fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import AwareDatetime, Field

from customer_signal.analytics.models import (
    AggregateDimension,
    AggregateResult,
    CatalogSourcesResult,
    CustomerJourneyResult,
    EvidenceResult,
    PatternMatchResult,
    RankCustomersResult,
)
from customer_signal.analytics.service import AnalyticsService
from customer_signal.domain.models import SourceId


type EnabledSources = Annotated[list[SourceId], Field(min_length=1, max_length=3)]
type ResultLimit = Annotated[int, Field(strict=True, ge=1, le=100)]
type CustomerId = Annotated[str, Field(min_length=1)]
type EvidenceId = Annotated[str, Field(min_length=1)]
type EvidenceIds = Annotated[list[EvidenceId], Field(min_length=1, max_length=100)]


_READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)


def create_mcp_server(service: AnalyticsService) -> FastMCP:
    """Create the bounded read-only MCP surface for an injected analytics service."""

    server = FastMCP(
        "Customer Signal Data",
        strict_input_validation=True,
        mask_error_details=True,
    )

    @server.tool(annotations=_READ_ONLY)
    def catalog_sources(
        start_at: AwareDatetime,
        end_at: AwareDatetime,
    ) -> CatalogSourcesResult:
        """Catalog sources with data in the half-open [start_at, end_at) time range."""

        return service.catalog_sources(start_at, end_at)

    @server.tool(annotations=_READ_ONLY)
    def aggregate_events(
        start_at: AwareDatetime,
        end_at: AwareDatetime,
        enabled_sources: EnabledSources,
        group_by: AggregateDimension = "source",
        limit: ResultLimit = 100,
    ) -> AggregateResult:
        """Aggregate a 1-to-3-source allowlist over [start_at, end_at), limited 1 to 100."""

        return service.aggregate_events(
            start_at,
            end_at,
            enabled_sources,
            group_by=group_by,
            limit=limit,
        )

    @server.tool(annotations=_READ_ONLY)
    def match_journey_pattern(
        start_at: AwareDatetime,
        end_at: AwareDatetime,
        enabled_sources: EnabledSources,
        limit: ResultLimit = 100,
    ) -> PatternMatchResult:
        """Match journeys in a 1-to-3-source allowlist over [start_at, end_at), limited 1 to 100."""

        return service.match_journey_pattern(
            start_at,
            end_at,
            enabled_sources,
            limit=limit,
        )

    @server.tool(annotations=_READ_ONLY)
    def rank_customers(
        start_at: AwareDatetime,
        end_at: AwareDatetime,
        enabled_sources: EnabledSources,
        limit: ResultLimit = 100,
    ) -> RankCustomersResult:
        """Rank customers in a 1-to-3-source allowlist over [start_at, end_at), limited 1 to 100."""

        return service.rank_customers(
            start_at,
            end_at,
            enabled_sources,
            limit=limit,
        )

    @server.tool(annotations=_READ_ONLY)
    def get_customer_journey(
        customer_id: CustomerId,
        start_at: AwareDatetime,
        end_at: AwareDatetime,
        enabled_sources: EnabledSources,
        limit: ResultLimit = 100,
    ) -> CustomerJourneyResult:
        """Get 1 to 100 events from an allowlist in the half-open [start_at, end_at) range."""

        return service.get_customer_journey(
            customer_id,
            start_at,
            end_at,
            enabled_sources,
            limit=limit,
        )

    @server.tool(annotations=_READ_ONLY)
    def get_evidence(evidence_ids: EvidenceIds) -> EvidenceResult:
        """Get masked evidence for 1 to 100 identifiers in caller-requested order."""

        return service.get_evidence(evidence_ids)

    return server


__all__ = ["create_mcp_server"]
