"""Tests for Trace Repository API with RBAC access control."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from api.covenant_api import (
    AccessLevel,
    TraceAccessContext,
    build_access_scope_filter,
    filter_trace_fields,
)


class TestAccessLevel:
    """Test AccessLevel enum."""

    def test_access_levels_exist(self):
        assert AccessLevel.FULL == "full"
        assert AccessLevel.PARTNER == "partner"
        assert AccessLevel.PUBLIC == "public"


class TestTraceAccessContext:
    """Test TraceAccessContext model."""

    def test_creates_context(self):
        ctx = TraceAccessContext(
            access_level=AccessLevel.PARTNER,
            user_id="user123",
            agent_scope=["agent1", "agent2"],
            partner_id="partner_abc",
        )
        assert ctx.access_level == AccessLevel.PARTNER
        assert ctx.user_id == "user123"
        assert ctx.agent_scope == ["agent1", "agent2"]
        assert ctx.partner_id == "partner_abc"

    def test_default_values(self):
        ctx = TraceAccessContext(
            access_level=AccessLevel.PUBLIC,
            user_id="anon",
        )
        assert ctx.agent_scope == []
        assert ctx.partner_id is None


class TestBuildAccessScopeFilter:
    """Test access scope SQL filter building."""

    def test_full_access_no_filter(self):
        ctx = TraceAccessContext(
            access_level=AccessLevel.FULL,
            user_id="admin",
        )
        sql, params, idx = build_access_scope_filter(ctx, 1)
        assert sql == ""
        assert params == []
        assert idx == 1

    def test_public_access_samples_only(self):
        ctx = TraceAccessContext(
            access_level=AccessLevel.PUBLIC,
            user_id="anonymous",
        )
        sql, params, idx = build_access_scope_filter(ctx, 1)
        assert "public_sample = TRUE" in sql
        assert params == []
        assert idx == 1

    def test_partner_access_with_agent_scope(self):
        ctx = TraceAccessContext(
            access_level=AccessLevel.PARTNER,
            user_id="partner_user",
            agent_scope=["agent1", "agent2"],
            partner_id="partner_abc",
        )
        sql, params, idx = build_access_scope_filter(ctx, 1)
        assert "agent_id_hash = ANY($1)" in sql
        assert "public_sample = TRUE" in sql
        assert "$2 = ANY(partner_access)" in sql
        assert params == [["agent1", "agent2"], "partner_abc"]
        assert idx == 3

    def test_partner_access_no_agent_scope(self):
        ctx = TraceAccessContext(
            access_level=AccessLevel.PARTNER,
            user_id="partner_user",
            agent_scope=[],
            partner_id="partner_abc",
        )
        sql, params, idx = build_access_scope_filter(ctx, 1)
        assert "public_sample = TRUE" in sql
        assert "$1 = ANY(partner_access)" in sql
        assert "agent_id_hash" not in sql  # No agent scope, so no agent filter
        assert params == ["partner_abc"]

    def test_partner_access_no_partner_id(self):
        ctx = TraceAccessContext(
            access_level=AccessLevel.PARTNER,
            user_id="partner_user",
            agent_scope=["agent1"],
            partner_id=None,
        )
        sql, params, idx = build_access_scope_filter(ctx, 1)
        assert "agent_id_hash = ANY($1)" in sql
        assert "public_sample = TRUE" in sql
        assert "partner_access" not in sql  # No partner ID, so no partner filter


class TestFilterTraceFields:
    """Test field filtering by access level."""

    def test_full_access_returns_all_fields(self):
        trace = {
            "trace_id": "trace-123",
            "agent": {"name": "Scout"},
            "dma_results": {"csdma": {"prompt_used": "secret prompt"}},
            "audit_signature": "sig123",
            "scrub_signature": "scrub_sig",
        }
        filtered = filter_trace_fields(trace, AccessLevel.FULL)
        assert filtered == trace

    def test_partner_access_excludes_audit_fields(self):
        trace = {
            "trace_id": "trace-123",
            "agent": {"name": "Scout"},
            "dma_results": {
                "csdma": {
                    "reasoning": "analysis here",
                    "prompt_used": "secret prompt",
                }
            },
            "audit_signature": "sig123",
            "scrub_signature": "scrub_sig",
            "scrub_key_id": "key123",
        }
        filtered = filter_trace_fields(trace, AccessLevel.PARTNER)
        assert "audit_signature" not in filtered
        assert "scrub_signature" not in filtered
        assert "scrub_key_id" not in filtered
        # DMA results should have prompts stripped
        assert filtered["dma_results"]["csdma"]["reasoning"] == "analysis here"
        assert "prompt_used" not in filtered["dma_results"]["csdma"]

    def test_public_access_returns_full_trace(self):
        # Public gets full details for sample traces
        trace = {
            "trace_id": "trace-123",
            "agent": {"name": "Scout"},
            "dma_results": {"csdma": {"reasoning": "analysis"}},
        }
        filtered = filter_trace_fields(trace, AccessLevel.PUBLIC)
        assert filtered == trace


class TestRepositoryEndpoints:
    """Test repository API endpoints."""

    @pytest.fixture
    def mock_db_pool(self):
        """Create a mock database pool."""
        from unittest.mock import MagicMock

        pool = MagicMock()
        conn = AsyncMock()
        pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
        pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)
        return pool, conn

    @pytest.mark.asyncio
    async def test_list_traces_public_access(self, mock_db_pool):
        """Test listing traces with public access."""
        pool, conn = mock_db_pool

        # Mock the query results
        conn.fetchval.return_value = 1  # count
        conn.fetch.return_value = [
            {
                "trace_id": "trace-public-1",
                "timestamp": datetime.now(UTC),
                "agent_name": "Scout",
                "agent_id_hash": "hash123",
                "thought_id": "thought1",
                "task_id": None,
                "trace_type": "standard",
                "trace_level": "full_traces",
                "cognitive_state": "work",
                "thought_type": "standard",
                "thought_depth": 0,
                "started_at": None,
                "completed_at": None,
                "csdma_plausibility_score": 0.9,
                "dsdma_domain_alignment": 0.85,
                "dsdma_domain": "Scout",
                "pdma_stakeholders": "user",
                "pdma_conflicts": None,
                "action_rationale": "reasoning here",
                "selected_action": "SPEAK",
                "action_success": True,
                "action_was_overridden": False,
                "idma_k_eff": 1.0,
                "idma_correlation_risk": 0.0,
                "idma_fragility_flag": True,
                "idma_phase": "rigidity",
                "conscience_passed": True,
                "entropy_passed": True,
                "coherence_passed": True,
                "optimization_veto_passed": True,
                "epistemic_humility_passed": True,
                "entropy_level": 0.5,
                "coherence_level": 0.8,
                "tokens_total": 1000,
                "cost_cents": 0.01,
                "models_used": ["llama"],
                "dma_results": {"csdma": {}},
                "conscience_result": {},
                "signature_verified": True,
                "pii_scrubbed": True,
                "original_content_hash": "hash",
                "audit_entry_id": None,
                "audit_sequence_number": None,
                "audit_entry_hash": None,
                "public_sample": True,
                "partner_access": [],
            }
        ]

        with patch("api.covenant_api.get_db_pool", return_value=pool):
            from api.covenant_api import list_repository_traces

            result = await list_repository_traces(
                access_level=AccessLevel.PUBLIC,
                user_id="anonymous",
            )

            assert "traces" in result
            assert "pagination" in result
            assert len(result["traces"]) == 1

    @pytest.mark.asyncio
    async def test_set_public_sample_requires_full_access(self, mock_db_pool):
        """Test that setting public sample requires full access."""
        pool, conn = mock_db_pool

        with patch("api.covenant_api.get_db_pool", return_value=pool):
            from fastapi import HTTPException

            from api.covenant_api import (
                PublicSampleRequest,
                set_trace_public_sample,
            )

            with pytest.raises(HTTPException) as exc_info:
                await set_trace_public_sample(
                    trace_id="trace-123",
                    request=PublicSampleRequest(public_sample=True),
                    access_level=AccessLevel.PARTNER,
                    user_id="partner_user",
                )

            assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_set_public_sample_full_access(self, mock_db_pool):
        """Test setting public sample with full access."""
        pool, conn = mock_db_pool
        conn.execute.return_value = "UPDATE 1"

        with patch("api.covenant_api.get_db_pool", return_value=pool):
            from api.covenant_api import (
                PublicSampleRequest,
                set_trace_public_sample,
            )

            result = await set_trace_public_sample(
                trace_id="trace-123",
                request=PublicSampleRequest(public_sample=True, reason="Good example"),
                access_level=AccessLevel.FULL,
                user_id="admin",
            )

            assert result["trace_id"] == "trace-123"
            assert result["public_sample"] is True

    @pytest.mark.asyncio
    async def test_set_partner_access_requires_full_access(self, mock_db_pool):
        """Test that setting partner access requires full access."""
        pool, conn = mock_db_pool

        with patch("api.covenant_api.get_db_pool", return_value=pool):
            from fastapi import HTTPException

            from api.covenant_api import (
                PartnerAccessRequest,
                set_trace_partner_access,
            )

            with pytest.raises(HTTPException) as exc_info:
                await set_trace_partner_access(
                    trace_id="trace-123",
                    request=PartnerAccessRequest(partner_ids=["partner_abc"]),
                    access_level=AccessLevel.PUBLIC,
                    user_id="anonymous",
                )

            assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_set_partner_access_add(self, mock_db_pool):
        """Test adding partner access."""
        pool, conn = mock_db_pool
        conn.fetchval.return_value = ["existing_partner"]

        with patch("api.covenant_api.get_db_pool", return_value=pool):
            from api.covenant_api import (
                PartnerAccessRequest,
                set_trace_partner_access,
            )

            result = await set_trace_partner_access(
                trace_id="trace-123",
                request=PartnerAccessRequest(
                    partner_ids=["new_partner"],
                    action="add",
                ),
                access_level=AccessLevel.FULL,
                user_id="admin",
            )

            assert "existing_partner" in result["partner_access"]
            assert "new_partner" in result["partner_access"]

    @pytest.mark.asyncio
    async def test_set_partner_access_remove(self, mock_db_pool):
        """Test removing partner access."""
        pool, conn = mock_db_pool
        conn.fetchval.return_value = ["partner_a", "partner_b"]

        with patch("api.covenant_api.get_db_pool", return_value=pool):
            from api.covenant_api import (
                PartnerAccessRequest,
                set_trace_partner_access,
            )

            result = await set_trace_partner_access(
                trace_id="trace-123",
                request=PartnerAccessRequest(
                    partner_ids=["partner_a"],
                    action="remove",
                ),
                access_level=AccessLevel.FULL,
                user_id="admin",
            )

            assert "partner_a" not in result["partner_access"]
            assert "partner_b" in result["partner_access"]

    @pytest.mark.asyncio
    async def test_get_statistics(self, mock_db_pool):
        """Test getting repository statistics."""
        pool, conn = mock_db_pool
        conn.fetchrow.return_value = {
            "trace_count": 100,
            "agent_count": 5,
            "domain_count": 3,
            "avg_plausibility": 0.85,
            "avg_alignment": 0.82,
            "avg_k_eff": 1.2,
            "conscience_pass_rate": 0.97,
            "override_rate": 0.02,
            "fragility_rate": 0.15,
        }
        conn.fetch.side_effect = [
            # Actions
            [
                {"selected_action": "SPEAK", "count": 65},
                {"selected_action": "OBSERVE", "count": 35},
            ],
            # By domain (empty for domain filter case)
            [],
        ]

        with patch("api.covenant_api.get_db_pool", return_value=pool):
            from api.covenant_api import get_repository_statistics

            result = await get_repository_statistics(
                access_level=AccessLevel.PUBLIC,
                domain="Scout",
            )

            assert result["totals"]["traces"] == 100
            assert result["scores"]["csdma_plausibility"]["mean"] == 0.85
            assert "SPEAK" in result["actions"]["distribution"]


class TestAccessScopeEdgeCases:
    """Test edge cases in access scope enforcement."""

    def test_empty_agent_scope_partner(self):
        """Partner with no agent scope still sees public samples."""
        ctx = TraceAccessContext(
            access_level=AccessLevel.PARTNER,
            user_id="partner_user",
            agent_scope=[],
            partner_id=None,
        )
        sql, params, idx = build_access_scope_filter(ctx, 1)
        assert "public_sample = TRUE" in sql

    def test_partner_scope_is_or_condition(self):
        """Partner scope should use OR conditions."""
        ctx = TraceAccessContext(
            access_level=AccessLevel.PARTNER,
            user_id="partner_user",
            agent_scope=["agent1"],
            partner_id="partner_abc",
        )
        sql, _, _ = build_access_scope_filter(ctx, 1)
        # Should be: (agent_id = ANY(...) OR public_sample = TRUE OR partner_id = ANY(...))
        assert " OR " in sql

    def test_param_index_increments_correctly(self):
        """Parameter index should increment for each param added."""
        ctx = TraceAccessContext(
            access_level=AccessLevel.PARTNER,
            user_id="partner_user",
            agent_scope=["agent1"],
            partner_id="partner_abc",
        )
        sql, params, idx = build_access_scope_filter(ctx, 5)
        # Started at 5, added 2 params (agent_scope, partner_id)
        assert idx == 7
        assert "$5" in sql or "$6" in sql
