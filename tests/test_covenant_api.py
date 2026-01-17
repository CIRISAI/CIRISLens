"""
Tests for CIRIS Covenant API - Trace Levels, IDMA, and Correlation Metadata.

Tests cover:
- CorrelationMetadata model validation
- CovenantEventsRequest with trace_level and correlation_metadata
- extract_trace_metadata function including IDMA field extraction
"""

from datetime import UTC, datetime

import pytest

from api.covenant_api import (
    CorrelationMetadata,
    CovenantEventsRequest,
    CovenantTrace,
    CovenantTraceEvent,
    TraceComponent,
    extract_trace_metadata,
)

# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def sample_trace_components():
    """Create sample trace components for testing."""
    return [
        TraceComponent(
            component_type="observation",
            event_type="THOUGHT_START",
            timestamp="2026-01-15T14:00:00+00:00",
            data={
                "thought_type": "standard",
                "thought_depth": 1,
                "task_priority": 0,
            },
        ),
        TraceComponent(
            component_type="context",
            event_type="SNAPSHOT_AND_CONTEXT",
            timestamp="2026-01-15T14:00:01+00:00",
            data={
                "cognitive_state": "active",
                "system_snapshot": {
                    "agent_identity": {"agent_id": "TestAgent"},
                },
            },
        ),
        TraceComponent(
            component_type="rationale",
            event_type="DMA_RESULTS",
            timestamp="2026-01-15T14:00:02+00:00",
            data={
                "csdma": {"plausibility_score": 0.9},
                "dsdma": {"domain_alignment": 0.85, "domain": "general"},
                "pdma": {"stakeholders": "user, system", "conflicts": "none"},
                "idma": {
                    "k_eff": 2.5,
                    "correlation_risk": 0.15,
                    "fragility_flag": False,
                    "phase": "healthy",
                    "sources_identified": ["source1", "source2", "source3"],
                    "correlation_factors": ["shared_training"],
                },
            },
        ),
        TraceComponent(
            component_type="rationale",
            event_type="ASPDMA_RESULT",
            timestamp="2026-01-15T14:00:03+00:00",
            data={
                "selected_action": "HandlerActionType.SPEAK",
                "action_rationale": "User requested information",
                "is_recursive": False,
            },
        ),
        TraceComponent(
            component_type="conscience",
            event_type="CONSCIENCE_RESULT",
            timestamp="2026-01-15T14:00:04+00:00",
            data={
                "conscience_passed": True,
                "action_was_overridden": False,
                "epistemic_data": {
                    "entropy_level": 0.1,
                    "coherence_level": 0.95,
                    "uncertainty_acknowledged": True,
                    "reasoning_transparency": 0.9,
                },
                "entropy_passed": True,
                "coherence_passed": True,
                "optimization_veto_passed": True,
                "epistemic_humility_passed": True,
            },
        ),
        TraceComponent(
            component_type="action",
            event_type="ACTION_RESULT",
            timestamp="2026-01-15T14:00:05+00:00",
            data={
                "action_executed": "speak",
                "execution_success": True,
                "execution_time_ms": 150.5,
                "audit_sequence_number": 42,
                "audit_entry_hash": "abc123def456",
                "tokens_total": 1500,
                "cost_cents": 0.15,
            },
        ),
    ]


@pytest.fixture
def sample_trace(sample_trace_components):
    """Create a sample trace for testing."""
    return CovenantTrace(
        trace_id="trace-test-12345",
        thought_id="th_test_abc",
        task_id="VERIFY_IDENTITY_test-uuid",
        agent_id_hash="agent_hash_123",
        started_at="2026-01-15T14:00:00+00:00",
        completed_at="2026-01-15T14:00:05+00:00",
        components=sample_trace_components,
        signature="test_signature_base64",
        signature_key_id="wa-2025-test",
    )


@pytest.fixture
def fragile_idma_trace_components():
    """Create trace components with fragile IDMA (k_eff < 2)."""
    return [
        TraceComponent(
            component_type="observation",
            event_type="THOUGHT_START",
            timestamp="2026-01-15T14:00:00+00:00",
            data={"thought_type": "follow_up", "thought_depth": 3},
        ),
        TraceComponent(
            component_type="rationale",
            event_type="DMA_RESULTS",
            timestamp="2026-01-15T14:00:01+00:00",
            data={
                "csdma": {"plausibility_score": 0.7},
                "dsdma": {"domain_alignment": 0.6, "domain": "healthcare"},
                "idma": {
                    "k_eff": 1.2,
                    "correlation_risk": 0.8,
                    "fragility_flag": True,
                    "phase": "fragile",
                },
            },
        ),
    ]


# =============================================================================
# CorrelationMetadata Model Tests
# =============================================================================


class TestCorrelationMetadata:
    """Tests for CorrelationMetadata Pydantic model."""

    def test_empty_metadata(self):
        """Test creating empty correlation metadata."""
        metadata = CorrelationMetadata()
        assert metadata.deployment_region is None
        assert metadata.deployment_type is None
        assert metadata.agent_role is None
        assert metadata.agent_template is None

    def test_full_metadata(self):
        """Test creating fully populated correlation metadata."""
        metadata = CorrelationMetadata(
            deployment_region="na",
            deployment_type="business",
            agent_role="customer_support",
            agent_template="discord-moderator",
        )
        assert metadata.deployment_region == "na"
        assert metadata.deployment_type == "business"
        assert metadata.agent_role == "customer_support"
        assert metadata.agent_template == "discord-moderator"

    def test_partial_metadata(self):
        """Test creating partially populated metadata."""
        metadata = CorrelationMetadata(
            deployment_region="eu",
            agent_role="coding",
        )
        assert metadata.deployment_region == "eu"
        assert metadata.deployment_type is None
        assert metadata.agent_role == "coding"
        assert metadata.agent_template is None

    def test_valid_deployment_regions(self):
        """Test various valid deployment regions."""
        regions = ["na", "eu", "uk", "apac", "latam", "mena", "africa", "oceania"]
        for region in regions:
            metadata = CorrelationMetadata(deployment_region=region)
            assert metadata.deployment_region == region

    def test_valid_deployment_types(self):
        """Test various valid deployment types."""
        types = ["personal", "business", "research", "nonprofit"]
        for dtype in types:
            metadata = CorrelationMetadata(deployment_type=dtype)
            assert metadata.deployment_type == dtype

    def test_valid_agent_roles(self):
        """Test various valid agent roles."""
        roles = [
            "assistant",
            "customer_support",
            "content",
            "coding",
            "research",
            "education",
            "moderation",
            "automation",
            "other",
        ]
        for role in roles:
            metadata = CorrelationMetadata(agent_role=role)
            assert metadata.agent_role == role

    def test_model_dump(self):
        """Test serialization of metadata."""
        metadata = CorrelationMetadata(
            deployment_region="na",
            deployment_type="business",
        )
        dumped = metadata.model_dump()
        assert dumped["deployment_region"] == "na"
        assert dumped["deployment_type"] == "business"
        assert dumped["agent_role"] is None
        assert dumped["agent_template"] is None

    def test_model_dump_exclude_none(self):
        """Test serialization excluding None values."""
        metadata = CorrelationMetadata(
            deployment_region="eu",
        )
        dumped = metadata.model_dump(exclude_none=True)
        assert dumped == {"deployment_region": "eu"}


# =============================================================================
# CovenantEventsRequest Model Tests
# =============================================================================


class TestCovenantEventsRequest:
    """Tests for CovenantEventsRequest Pydantic model."""

    def test_minimal_request(self, sample_trace):
        """Test creating minimal request with defaults."""
        event = CovenantTraceEvent(event_type="complete_trace", trace=sample_trace)
        request = CovenantEventsRequest(
            events=[event],
            batch_timestamp=datetime(2026, 1, 15, 14, 0, 0, tzinfo=UTC),
            consent_timestamp=datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC),
        )
        assert request.trace_level == "generic"
        assert request.correlation_metadata is None
        assert len(request.events) == 1

    def test_request_with_trace_level_generic(self, sample_trace):
        """Test request with explicit generic trace level."""
        event = CovenantTraceEvent(event_type="complete_trace", trace=sample_trace)
        request = CovenantEventsRequest(
            events=[event],
            batch_timestamp=datetime.now(UTC),
            consent_timestamp=datetime.now(UTC),
            trace_level="generic",
        )
        assert request.trace_level == "generic"

    def test_request_with_trace_level_detailed(self, sample_trace):
        """Test request with detailed trace level."""
        event = CovenantTraceEvent(event_type="complete_trace", trace=sample_trace)
        request = CovenantEventsRequest(
            events=[event],
            batch_timestamp=datetime.now(UTC),
            consent_timestamp=datetime.now(UTC),
            trace_level="detailed",
        )
        assert request.trace_level == "detailed"

    def test_request_with_trace_level_full_traces(self, sample_trace):
        """Test request with full_traces trace level."""
        event = CovenantTraceEvent(event_type="complete_trace", trace=sample_trace)
        request = CovenantEventsRequest(
            events=[event],
            batch_timestamp=datetime.now(UTC),
            consent_timestamp=datetime.now(UTC),
            trace_level="full_traces",
        )
        assert request.trace_level == "full_traces"

    def test_request_with_correlation_metadata(self, sample_trace):
        """Test request with correlation metadata."""
        event = CovenantTraceEvent(event_type="complete_trace", trace=sample_trace)
        correlation = CorrelationMetadata(
            deployment_region="na",
            deployment_type="business",
            agent_role="customer_support",
        )
        request = CovenantEventsRequest(
            events=[event],
            batch_timestamp=datetime.now(UTC),
            consent_timestamp=datetime.now(UTC),
            trace_level="detailed",
            correlation_metadata=correlation,
        )
        assert request.correlation_metadata is not None
        assert request.correlation_metadata.deployment_region == "na"
        assert request.correlation_metadata.deployment_type == "business"
        assert request.correlation_metadata.agent_role == "customer_support"

    def test_request_multiple_events(self, sample_trace):
        """Test request with multiple events."""
        events = [
            CovenantTraceEvent(event_type="complete_trace", trace=sample_trace),
            CovenantTraceEvent(event_type="complete_trace", trace=sample_trace),
            CovenantTraceEvent(event_type="complete_trace", trace=sample_trace),
        ]
        request = CovenantEventsRequest(
            events=events,
            batch_timestamp=datetime.now(UTC),
            consent_timestamp=datetime.now(UTC),
        )
        assert len(request.events) == 3


# =============================================================================
# extract_trace_metadata Function Tests
# =============================================================================


class TestExtractTraceMetadata:
    """Tests for extract_trace_metadata function."""

    def test_basic_extraction(self, sample_trace):
        """Test basic metadata extraction from trace."""
        metadata = extract_trace_metadata(sample_trace)

        # trace_id is not extracted (stored separately in DB)
        assert "trace_id" not in metadata
        assert metadata["thought_id"] == "th_test_abc"
        assert metadata["task_id"] == "VERIFY_IDENTITY_test-uuid"
        assert metadata["agent_id_hash"] == "agent_hash_123"

    def test_trace_level_default(self, sample_trace):
        """Test default trace level is generic."""
        metadata = extract_trace_metadata(sample_trace)
        assert metadata["trace_level"] == "generic"

    def test_trace_level_explicit(self, sample_trace):
        """Test explicit trace level passed to function."""
        metadata = extract_trace_metadata(sample_trace, trace_level="detailed")
        assert metadata["trace_level"] == "detailed"

        metadata = extract_trace_metadata(sample_trace, trace_level="full_traces")
        assert metadata["trace_level"] == "full_traces"

    def test_trace_type_detection_verify_identity(self, sample_trace):
        """Test trace type detection from task_id."""
        metadata = extract_trace_metadata(sample_trace)
        assert metadata["trace_type"] == "VERIFY_IDENTITY"

    def test_trace_type_detection_validate_integrity(self, sample_trace_components):
        """Test trace type detection for VALIDATE_INTEGRITY."""
        trace = CovenantTrace(
            trace_id="trace-test",
            thought_id="th_test",
            task_id="VALIDATE_INTEGRITY_uuid",
            started_at="2026-01-15T14:00:00+00:00",
            completed_at="2026-01-15T14:00:05+00:00",
            components=sample_trace_components,
            signature="sig",
            signature_key_id="key",
        )
        metadata = extract_trace_metadata(trace)
        assert metadata["trace_type"] == "VALIDATE_INTEGRITY"

    def test_thought_start_extraction(self, sample_trace):
        """Test extraction of THOUGHT_START data."""
        metadata = extract_trace_metadata(sample_trace)

        assert metadata["thought_type"] == "standard"
        assert metadata["thought_depth"] == 1
        assert metadata["thought_start"] is not None

    def test_snapshot_context_extraction(self, sample_trace):
        """Test extraction of SNAPSHOT_AND_CONTEXT data."""
        metadata = extract_trace_metadata(sample_trace)

        assert metadata["cognitive_state"] == "active"
        assert metadata["agent_name"] == "TestAgent"
        assert metadata["snapshot_and_context"] is not None

    def test_csdma_extraction(self, sample_trace):
        """Test extraction of CSDMA (Common Sense DMA) data."""
        metadata = extract_trace_metadata(sample_trace)

        assert metadata["csdma_plausibility_score"] == 0.9

    def test_dsdma_extraction(self, sample_trace):
        """Test extraction of DSDMA (Domain-Specific DMA) data."""
        metadata = extract_trace_metadata(sample_trace)

        assert metadata["dsdma_domain_alignment"] == 0.85
        assert metadata["dsdma_domain"] == "general"

    def test_pdma_extraction(self, sample_trace):
        """Test extraction of PDMA (Principled DMA) data."""
        metadata = extract_trace_metadata(sample_trace)

        assert metadata["pdma_stakeholders"] == "user, system"
        assert metadata["pdma_conflicts"] == "none"

    def test_idma_extraction_healthy(self, sample_trace):
        """Test extraction of IDMA data for healthy agent."""
        metadata = extract_trace_metadata(sample_trace)

        assert metadata["idma_k_eff"] == 2.5
        assert metadata["idma_correlation_risk"] == 0.15
        assert metadata["idma_fragility_flag"] is False
        assert metadata["idma_phase"] == "healthy"

    def test_idma_extraction_fragile(self, fragile_idma_trace_components):
        """Test extraction of IDMA data for fragile agent."""
        trace = CovenantTrace(
            trace_id="trace-fragile",
            thought_id="th_fragile",
            task_id="task-fragile",
            started_at="2026-01-15T14:00:00+00:00",
            completed_at="2026-01-15T14:00:05+00:00",
            components=fragile_idma_trace_components,
            signature="sig",
            signature_key_id="key",
        )
        metadata = extract_trace_metadata(trace)

        assert metadata["idma_k_eff"] == 1.2
        assert metadata["idma_correlation_risk"] == 0.8
        assert metadata["idma_fragility_flag"] is True
        assert metadata["idma_phase"] == "fragile"

    def test_idma_missing(self, sample_trace_components):
        """Test extraction when IDMA is not present."""
        # Remove IDMA from DMA_RESULTS
        for comp in sample_trace_components:
            if comp.event_type == "DMA_RESULTS":
                comp.data.pop("idma", None)

        trace = CovenantTrace(
            trace_id="trace-no-idma",
            thought_id="th_no_idma",
            task_id="task-no-idma",
            started_at="2026-01-15T14:00:00+00:00",
            completed_at="2026-01-15T14:00:05+00:00",
            components=sample_trace_components,
            signature="sig",
            signature_key_id="key",
        )
        metadata = extract_trace_metadata(trace)

        assert metadata["idma_k_eff"] is None
        assert metadata["idma_correlation_risk"] is None
        assert metadata["idma_fragility_flag"] is None
        assert metadata["idma_phase"] is None

    def test_aspdma_extraction(self, sample_trace):
        """Test extraction of ASPDMA (Action Selection) data."""
        metadata = extract_trace_metadata(sample_trace)

        # Should strip "HandlerActionType." prefix
        assert metadata["selected_action"] == "SPEAK"
        assert metadata["action_rationale"] == "User requested information"

    def test_conscience_extraction(self, sample_trace):
        """Test extraction of CONSCIENCE_RESULT data."""
        metadata = extract_trace_metadata(sample_trace)

        assert metadata["conscience_passed"] is True
        assert metadata["action_was_overridden"] is False
        assert metadata["entropy_level"] == 0.1
        assert metadata["coherence_level"] == 0.95
        assert metadata["uncertainty_acknowledged"] is True
        assert metadata["reasoning_transparency"] == 0.9
        assert metadata["entropy_passed"] is True
        assert metadata["coherence_passed"] is True
        assert metadata["optimization_veto_passed"] is True
        assert metadata["epistemic_humility_passed"] is True

    def test_action_result_extraction(self, sample_trace):
        """Test extraction of ACTION_RESULT data."""
        metadata = extract_trace_metadata(sample_trace)

        assert metadata["action_success"] is True
        assert metadata["processing_ms"] == 150.5
        assert metadata["audit_sequence_number"] == 42
        assert metadata["audit_entry_hash"] == "abc123def456"
        assert metadata["tokens_total"] == 1500
        assert metadata["cost_cents"] == 0.15

    def test_empty_components(self):
        """Test extraction with empty components list."""
        trace = CovenantTrace(
            trace_id="trace-empty",
            thought_id="th_empty",
            task_id="task-empty",
            started_at="2026-01-15T14:00:00+00:00",
            completed_at="2026-01-15T14:00:05+00:00",
            components=[],
            signature="sig",
            signature_key_id="key",
        )
        metadata = extract_trace_metadata(trace)

        # Should have basic fields but no component data
        assert metadata["thought_id"] == "th_empty"
        assert metadata["trace_level"] == "generic"
        assert metadata["csdma_plausibility_score"] is None
        assert metadata["idma_k_eff"] is None

    def test_all_idma_phases(self):
        """Test extraction of all IDMA phases."""
        phases = ["nascent", "emerging", "healthy", "fragile"]
        for phase in phases:
            components = [
                TraceComponent(
                    component_type="rationale",
                    event_type="DMA_RESULTS",
                    timestamp="2026-01-15T14:00:00+00:00",
                    data={"idma": {"phase": phase, "k_eff": 1.5}},
                ),
            ]
            trace = CovenantTrace(
                trace_id=f"trace-{phase}",
                thought_id=f"th_{phase}",
                task_id=f"task-{phase}",
                started_at="2026-01-15T14:00:00+00:00",
                completed_at="2026-01-15T14:00:05+00:00",
                components=components,
                signature="sig",
                signature_key_id="key",
            )
            metadata = extract_trace_metadata(trace)
            assert metadata["idma_phase"] == phase


# =============================================================================
# Integration Tests
# =============================================================================


class TestTraceLevelIntegration:
    """Integration tests for trace level handling."""

    def test_full_request_with_all_fields(self, sample_trace):
        """Test complete request with all optional fields populated."""
        correlation = CorrelationMetadata(
            deployment_region="eu",
            deployment_type="research",
            agent_role="research",
            agent_template="research-assistant",
        )
        event = CovenantTraceEvent(event_type="complete_trace", trace=sample_trace)
        request = CovenantEventsRequest(
            events=[event],
            batch_timestamp=datetime.now(UTC),
            consent_timestamp=datetime.now(UTC),
            trace_level="full_traces",
            correlation_metadata=correlation,
        )

        # Verify all fields are accessible
        assert request.trace_level == "full_traces"
        assert request.correlation_metadata.deployment_region == "eu"
        assert request.correlation_metadata.deployment_type == "research"
        assert request.correlation_metadata.agent_role == "research"
        assert request.correlation_metadata.agent_template == "research-assistant"

        # Verify trace extraction works with trace_level
        metadata = extract_trace_metadata(
            request.events[0].trace, trace_level=request.trace_level
        )
        assert metadata["trace_level"] == "full_traces"
        assert metadata["idma_k_eff"] == 2.5
        assert metadata["idma_phase"] == "healthy"

    def test_request_serialization(self, sample_trace):
        """Test that request can be serialized to JSON."""
        correlation = CorrelationMetadata(deployment_region="na")
        event = CovenantTraceEvent(event_type="complete_trace", trace=sample_trace)
        request = CovenantEventsRequest(
            events=[event],
            batch_timestamp=datetime.now(UTC),
            consent_timestamp=datetime.now(UTC),
            trace_level="detailed",
            correlation_metadata=correlation,
        )

        # Should be able to dump to dict (for JSON serialization)
        dumped = request.model_dump()
        assert dumped["trace_level"] == "detailed"
        assert dumped["correlation_metadata"]["deployment_region"] == "na"
        assert len(dumped["events"]) == 1
